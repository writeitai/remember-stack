# Split oversized chunks before embedding (D162)

**Status** Proposed. [Analysis](../analysis/split_oversized_chunks_analysis.md).

## 1. Check the complete embedding input

CHUNK and EMBED_CHUNK use the same size ceiling, a code constant shipped for the pinned
embedding model and provider route, matching P1's model and dimensions. A different ceiling
requires a new release. An unqualified self-host binding uses the conservative default constant.
Pin tokenizer, vocabulary and framing. Qualify full delivery on every route, including fallbacks,
for the full D80 render (normalized body, header and separators) plus framing. Pin per-input and
aggregate token, byte and item limits in the release. One nonblank Unicode scalar must fit with the largest
header and framing. Hosted Qwen3-Embedding-8B on OpenRouter `nebius/bf16` starts qualification
at **16,384 tokens**. The benchmark binding requires at least **22,528 tokens**.

CHUNK checks the [base candidates](e1_chunks_design.md#4-non-overlapping-chunks-within-sections-d58-d162)
for input and singleton-request fit. It keeps fitting spans and rechecks fit against final order and
count because a split can change the header. EMBED_CHUNK checks the same fit before carry-forward or
dispatch and subdivides ordered batches only as needed to fit request limits.

## 2. Split only failing candidates

Cut first at eligibility changes in nonblank text. Use E0 block eligibility and D133 gap labels.
Unlabelled gaps are eligible. Attach separator whitespace to the preceding piece and leading
whitespace to the first, preserving labels. For size cuts, prefer block seams, recorded structural
seams (turn, page, table row), sentence, line, whitespace, then Unicode scalar boundaries.
Take the legal seam nearest the midpoint in the first available tier, breaking ties at the earlier
offset. Keep CRLF together and nonblank text on both sides. Repeat full fit checks and cuts.
Concatenating pieces reproduces the candidate's exact bytes.

Pieces use UUIDv4, consecutive document ordinals, exact half-open E0 spans within the same
section, and legacy whitespace token counts. Store inclusive first and last intersected block
ordinals, or the preceding block in both fields for a gap-only piece. Block ordinals may repeat.
Clip source maps and converter origins. Section history and resolved document references find
the chunk containing the first block's start, preserving version scope, representation and order.

Use existing key builders on the final grid. Hash exact ordered block fragments and gap bytes
with a split discriminator. Selection and Claimify label nonblank gap-only windows by exact
span even beside block-backed neighbours. Ground Claimify's kept propositions with exact spans and D80
typed locations. D122 reads the preceding eight Selection producers in final order, including empty results.
[Passage labels](../../src/rememberstack/core/source_passages.py#L124-L168) · [Producer order](../../src/rememberstack/workers/extraction_references.py#L56-L76).

## 3. Grid-wide reuse gate

A grid is the chunks for one representation and chunker version. A grid contains a split when
any row has immutable `span_kind = 'split'`. Intact candidates and legacy rows use `whole`.
For D140, CHUNK checks the final in-memory grid before inserting all its rows together. Later
stages use the saved grid. Skip reuse for a split receiver. Filter split donors by their own
grid before ranking in the existing lookups.

| Stage | Existing lookup |
|---|---|
| E1 vector carry-forward | [`ChunkCatalog.carry_forward_sources`](../../src/rememberstack/spine/chunk_catalog.py#L209-L243) |
| E2 Selection | [`SelectionCatalog.prior`](../../src/rememberstack/spine/selection_catalog.py#L102-L138) |
| E2 Claimify | [`ClaimCatalog.prior_extracted_chunk`](../../src/rememberstack/spine/claim_catalog.py#L62-L100) |
| D140 text origin | [`ChunkCatalog.text_origin_matches`](../../src/rememberstack/spine/chunk_catalog.py#L82-L116) |

Grid membership and span kind are immutable, and grids are never repaired. A split in another
representation of the same version leaves the donor's grid status fixed through commit, so the lookup filter
suffices. All missing results on a split grid run fresh under existing skip rules. Chunks take
their version's `source_modified_at` or `published_at` (`NULL` if undated). Committed results
replay by chunk ID. Unsplit grids keep legacy keys, ranking and remapping, with identical rows
for equal inputs, allocations and eligible donors.

## 4. Rollout

Apply the additive column under [D160](packaging_distribution_design.md#61-migration-compatibility).
Pause STRUCTURE claims and let legacy E1 attempts and handoffs settle. Retire outgoing CHUNK,
EMBED_CHUNK, Selection, Claimify and both section-entry readers before enabling split writes and fit
checks together. Keep the chunker version and work keys. Retries hand off saved grids as-is.

## 5. Terminal failure

Each nonblank input needs a finite, nonzero vector of the configured dimension. `empty_body`
is the successful skip. Isolate failures as needed. Local fit failure or confirmed per-input size
rejection dead-letters with `embedding.input_too_large`. Other confirmed deterministic rejections
use `embedding.input_rejected`. Record chunk ID, rendered hash and binding in the existing terminal
path. Throttling, ambiguous 429s, outages and unusable responses take bounded retries. Keep committed batches.

A failed oversized grid stays saved. A keyless D55 ingest matching the latest non-deleted
version's bytes is a no-op. Changed bytes (including a revert) or a new D140 version key create
a successor. D159 forget and re-add with `allow_forgotten=true` starts a fresh lineage.
[Version registration](../../src/rememberstack/spine/document_catalog.py#L221-L250) · [D159 re-ingest](archive_and_forget_design.md#36-re-ingest-guard-and-adding-a-document-back).

## 6. Acceptance

Replay frozen LoCoMo Full-v45 and BEAM E0 artifacts, settings, prior reuse state, UUIDs and clocks
offline. Apply the additive schema to the checkpoint and remove target rows and completed work
from the candidate so CHUNK repacks. Match saved replies to exact ordered requests, including
routing and framing. Require zero splits and zero live calls. Compare every `chunks` and
`chunk_search` column, D122 producer order and stamps. Record fixture digests and the replay command.

Test deterministic cuts, exact bytes and provenance, gap-only grounding, header growth and D122
order, including empty Selection for ineligible pieces without a call. Both section-entry readers
retain fitting-grid IDs and order and return the piece containing the first block's start when split.

In a dated lineage, test unsplit → split → unsplit with matching intact chunks and same-date E2
matches, including empty results. Count E1 and both E2 calls and check dates and claim IDs. Verify
same-chunk replay, fallback to an older eligible donor or fresh work, and stable donor eligibility
when a second representation splits. Keep ordinary-edit D56 and D122 coverage. Rehearse rollout
with an above-cap saved grid, a CHUNK retry after grid write and EMBED_CHUNK in backoff. Test
replay, typed rejection versus retries, and D55/D140 no-op and successor cases.
