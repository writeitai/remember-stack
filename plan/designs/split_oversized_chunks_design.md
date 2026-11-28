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
**22,528 tokens**. Keep each binding active through its outstanding work.
Check persisted grids against the incoming binding while queries still use
the outgoing binding.
Online limit changes also preserve the outgoing query request envelope.
[D80 maintenance cutover](e1_embedding_input_policy.md#45-migration-triggers-and-vector-reuse).

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
source map and converter-origin ranges to each piece. Section history and
resolved document references use the chunk whose half-open span contains the
start of the keyed section's first block. Preserve each reader's version and
representation scope and result order.
[D140 section keys](effective_time_and_section_references_design.md#42-storage-and-uniqueness).

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
CHUNK sets it in the grid transaction if the final grid contains a split.
Once set, it stays true across the version's generations and representations.

All four lookups require a false flag on both versions, accounting for the
receiver's pending CHUNK grid. Filter donors before ranking. Use the lineage
row in `documents` to serialize reuse with flag writes. Under an exclusive
lock, CHUNK rechecks for a saved grid, resolves D140 origins and commits the
grid and flag together. EMBED_CHUNK, Selection and Claimify take a shared
lock, re-read both flags and hold the lock through publication. If a candidate
fails the check, select an eligible donor or process fresh. Commit a carried
vector and its authority stamps in one PostgreSQL transaction. Keep artifact
reads and provider calls outside these locks.

| Stage | Existing lookup to filter |
|---|---|
| E1 vector carry-forward | [`ChunkCatalog.carry_forward_sources` / `_SELECT_CARRY_FORWARD`](../../src/rememberstack/spine/chunk_catalog.py#L422-L440), called by [EMBED_CHUNK](../../src/rememberstack/workers/e1.py#L281-L289) |
| E2 Selection | [`SelectionCatalog.prior`](../../src/rememberstack/spine/selection_catalog.py#L102-L138), called by [`_reuse_prior_selection`](../../src/rememberstack/workers/e2.py#L655-L674) |
| E2 Claimify | [`ClaimCatalog.prior_extracted_chunk` / `_SELECT_PRIOR_EXTRACTED`](../../src/rememberstack/spine/claim_catalog.py#L425-L448), called by [`_reuse_prior_claimify`](../../src/rememberstack/workers/e2.py#L817-L841) |
| D140 text origin | [`ChunkCatalog.text_origin_matches` / `_SELECT_TEXT_ORIGIN_MATCHES`](../../src/rememberstack/spine/chunk_catalog.py#L332-L346), called by [`ChunkHandler._text_origins`](../../src/rememberstack/workers/e1.py#L193-L217) |

Once a version is marked split, missing E1/E2 results are computed under
existing skip rules. New chunks take that version's `source_modified_at` or
`published_at` (`NULL` if undated). Committed results replay by chunk ID even
after the flag is set. Unsplit versions keep today's keys, ranking and
remapping, with byte-identical rows for equal inputs, allocations and eligible
donors.

## 4. Rollout

Install the version flag and replay guard compatibly under D160.
Pause STRUCTURE claims and settle running attempts. Fence CHUNK and
EMBED_CHUNK `dead_letter` to `pending` transitions in PostgreSQL across all
callers and lanes. Serialize the fence with in-flight resets. A
`BEFORE UPDATE` trigger skips the individual reset when the fence is active
or the row is already `replay_blocked`. A skipped row is absent from
`RETURNING`. This lets D159 unarchive restore testimony and wake eligible
work in one transaction during the initial rollout, including on the outgoing
release. Later binding switches
[fence actual undos](e1_embedding_input_policy.md#45-migration-triggers-and-vector-reuse)
so eligible E1 resets can commit with the undo.

Use the outgoing workers and binding to drain all legacy jobs of both stages
to terminal status, including delayed jobs, retries, leases, saved-grid CHUNK
handoffs and replays committed before the fence. Mark remaining legacy E1 dead
letters `replay_blocked` while fenced. The trigger keeps applying the permanent
block after the fence lifts.
Retire previous-release CHUNK, EMBED_CHUNK, Selection and Claimify workers
and section-entry readers. Turn on split writes and fit enforcement together,
then resume admission and eligible replay.
[Replay](orchestration_design.md#6-dead-letter-operations) ·
[Unarchive](archive_and_forget_design.md#23-undo-and-coming-back) ·
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
dead letters stay pipeline-failed. At the same D55 source identity, a
keyless ingest is a no-op when its bytes match the latest version and that
version is non-deleted. A revert to bytes matching only an older version
or a new D140 `version_key` creates a successor. D159 forget and re-add
with `allow_forgotten=true` starts a fresh lineage.
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
provenance, header growth and D122 order. Run `section_history` and
`document_references` on a fitting grid and compare ordered
`first_chunk_ids` to the legacy readers. On a grid split inside a keyed
section's first block, both return exactly the ID of the piece containing
that block's start.

In a dated lineage, count E1 and both E2 calls and check dates and claim IDs
through unsplit → split → unsplit. Exercise matching keys for a distant intact
chunk, including same-date E2 matches and empty results. The split target
runs fresh. Retry its committed results by chunk ID with zero model calls.
The next version skips the split donor, taking an older eligible match or
running fresh at its own date. Retain D56 packing and D122 coverage for
ordinary edits.

Race each reuse path against a split in a new representation of the donor
or receiver. A flag set before publication requires an eligible fallback
or fresh work. When reuse commits first, its result replays by chunk ID.
Check that a refused copy leaves neither a P1 row nor a stamp before fallback.

Rehearse cutover with an above-cap legacy grid, CHUNK retry after grid
write and EMBED_CHUNK in backoff. Exercise temporary and permanent replay
blocks in both CLIs. The outgoing CLI exits with `internal_error`. The
incoming CLI gives the [typed refusal](orchestration_design.md#6-dead-letter-operations).
Both preserve the row and announce no work. Test outgoing unarchive with
mixed cut-short work during the fence and with the permanent block alone. Require
testimony restoration, wakes for eligible resets and unchanged blocked
dead letters with no wake. Eligible post-cutover work can replay. Test typed
rejection versus bounded retries, latest-byte no-op and successors on reverts
and new D140 keys.

Rehearse a binding switch after all work settles. Reject incoming caps that
fail fit for a completed grid or for an input reached only by eligible replay.
Also reject a lower shared byte cap when every saved passage fits but a
previously accepted semantic query would fail. Issue that query again in a
fresh request and verify it succeeds on the outgoing binding and vectors.
A fitting E1-local batch-item-limit change and a shared limit change that
preserves the query envelope both publish with queries succeeding throughout
and zero vector writes or rebuild calls. A fitting generation change
completes D80 rebuild and verification.

Race unarchive with the later fence. Resets committed before installation
drain with outgoing work. During the fence, same-byte connector observations
of live and caller-archived documents return unchanged without resets or
wakes. An actual undo returns a retryable refusal and preserves the archive
and its E1 dead letters. After publication or rollback, retry the undo and
require eligible resets and wakes in the testimony-restoration transaction.
