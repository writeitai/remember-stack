# Split oversized chunks before embedding (D162)

**Status** Proposed. [Incident and benchmark analysis](../analysis/split_oversized_chunks_analysis.md).

## 1. Measure the actual input

Before admitting a binding for new E1 work, pin the model and dimensions,
tokenizer code and data, request framing and special tokens, gateway and
routing settings. Qualify a common per-input token ceiling at which every
reachable route delivers the full input, including on fallback. Fit means
meeting that ceiling and fitting alone under the qualified request limits.
Prove fit for any one nonblank Unicode scalar with the largest D80 header
and framing. Obey request-wide item, token and serialized byte limits when
batching. Start hosted Qwen3-Embedding-8B qualification at 16,384 tokens
on OpenRouter `nebius/bf16`.
[Embedding input policy](e1_embedding_input_policy.md#4-embedding-input-policy).

CHUNK runs the existing section-bounded, anchored packer first. Render each
candidate with its actual D80 facts, including chunk count, and check the
full normalized body, header, and separators with the pinned tokenizer and
framing. If all fit, use the existing row assembly and allocation. For
fitting inputs and reuse history with no split pieces, every `chunks` and
`chunk_search` field has its legacy value with the same inputs and allocations.
The base chunker, embedding and extraction generations stay in use.
Delivery keys follow §3 regardless of whether a grid contains splits.

EMBED_CHUNK checks every prepared input against the active qualified binding
before reuse or dispatch. This catches a title or route change between
packing and dispatch. An over-ceiling input fails with the typed size error
in §4. Every dispatched input is complete.

## 2. Refine only failing candidates

- Keep each candidate's section owner and extent. First force cuts where
  nonblank extraction eligibility changes, including in labelled gaps.
  Unlabelled text inherits the parent's eligibility. Keep CRLF together.
  Attach separator whitespace to the preceding piece when possible and to
  the following piece at the start, with its original provenance. Each
  piece must contain nonblank source text.
- For a range that fails the size check, cut at the legal seam nearest the
  scalar midpoint in the first available tier. Prefer existing block
  boundaries, then recorded structural seams such as turns, pages or table
  rows, then sentence, line and whitespace boundaries in that order. Break
  ties at the earlier offset. With no seam, cut at the nearest Unicode
  scalar boundary that leaves nonblank text on both sides. Pin the seam
  rules. Render and check each child, recursing on failures. D80 can add a
  larger header to a shorter body, so check the rendered child even at a
  preferred seam.
- Use multi-chunk D80 mode rules once a split is needed. If an oversized
  singleton would fit whole in that mode, still cut it into at least two
  pieces. Each split makes progress toward the qualified one-scalar case.
  Assign consecutive document ordinals and verify every final render with
  the final count before committing.

The pieces concatenate byte for byte to the original candidate span. Store
half-open Unicode code-point offsets into immutable E0 Markdown and the
first and last intersected block ordinals, using the preceding block for a
piece wholly in a gap. Keep source locators and clip their coverage to the
piece. For E2, label an exact gap-only window even if another window has
a clipped block.
Use the ordinary D119 grounding and D122 prior-eight window on the final
grid. For each D162 split grid, section references and history pick the
lowest-ordinal chunk whose exact span intersects the section's first block.
Apply this within the representation and full packing generation. Unsplit
grids contribute all their matching IDs in the existing result order.

Use UUIDv5 for replacement pieces, with the representation ID as namespace
and an unambiguous tuple of the full `chunker_version(params=...)`, split-rule
version and half-open span as the name. The full generation includes the
algorithm and all packing parameters. P1 upserts and E2 jobs key on chunk IDs,
so the same span in another generation needs a distinct ID. Retries of a span
within a generation reuse its ID. Store the configured packing generation for
the entire grid. Use `d162:<SHA-256 of exact UTF-8 slice>` as the piece's
content hash, including whole-block prefixes emitted by a split.

Each split piece starts its own embedding and E2 completion. Only intact
chunks participate in carry-forward reuse. An eligible split piece uses its ID
in its Selection key because a fresh run can produce different cards.
An ineligible split piece uses a separately tagged hash of its exact UTF-8
slice and `EXTRACTION_ELIGIBILITY_POLICY_VERSION` as its Selection key. Each
new ineligible row records a fresh empty Selection and Claimify completion
without a model call. Every producer keeps its slot in the ordered D122
window, and an unchanged empty producer has the same key across versions.
A new split piece takes the version's D140 source date or unknown, with its
date-free lookup key unset. Ordinary retries use completions of the same row.

For intact chunks, start with the legacy E1 key inputs from the unsplit
candidates. They include document-adjacent chunks that share the target's
section. E2 reads the closest same-section chunk on each side, including
across a nested section. Use the legacy calculation unless an eligible intact
target consumes a split neighbor in either grid or a prior row has tagged
D162 extraction keys.

Align intact targets by their own block hashes within the lineage. Read the
prior target's recorded representation and packing generation. Compare the
exact Markdown, clipped D119 passage ranges and converter-origin coverage in
each target, previous and next slot using window-relative coordinates. Keep
absent slots in place. Compare the typed D80 location pairs E2 admits and the
other date-free key inputs.
When they match, keep the prior `reuse_identity_hash`, `extraction_input_hash`
and `text_origin_at`. On a new version, choose among matching rows in earlier
non-deleted versions using D140's date eligibility and ordering. An undated
version can keep matching keys with an unknown date.

Without a match, hash the final stable inputs. Resolve the date under D140
for a new version and use the effective date in §3 for recovery. Include it
in the Selection key. Tag both new keys `d162:` so later grids can recognize
them even after the neighbor fits. For this reuse path, verify the inputs
of the row supplying the result and remap evidence under D119. Claimify
also checks its ordered D122 producer keys.
[E1 reuse](e1_chunks_design.md#7-reuse-mechanics-d56-bound-here-amendments-a1a3) ·
[D140 text origin](effective_time_and_section_references_design.md#5-reuse-across-versions-text-origin-time).

## 3. Recover through CHUNK while the grid is unpublished

A grid is the pair `(representation_id, full chunker_version)` for a
document version. CHUNK and `embed_chunk` record that pair at enqueue and
use it at dispatch. Keep each stage's existing ledger key for the grid it
identifies. Under the lineage lock, use the base component version for the
first grid at an unused key. For each additional grid, append an unambiguous
encoding of the pair to the base component version. Repeated enqueues for
the pair resolve to its assigned key. Legacy work without a recorded
generation counts for a grid only if retained evidence identifies the pair.
The D56 input keys still govern paid reuse.

E1 completion requires success of that grid's embed row. Carry its pair
through the claims handoff, including deferred D143 admission. E2 uses it
for Selection and Claimify dispatch, windows and barriers through the
normalization fan-out. For older chunk-grain E2 work, the target chunk
identifies the pair. Version-level readiness and receipts select their
expected representation and packing generation first, then read completion
from that grid's work and barriers. The empty path also requires success
of that exact grid's embed row.

Recovery starts from the failed attempt's pair. Leave legacy work in the
dead letter queue (DLQ) if retained evidence cannot identify its grid.
E1-complete and previously readable grids keep their cuts and IDs. Verify
the typed size error or measure that the failed input exceeds its qualified
ceiling. Require separate evidence of continuous reader isolation, such
as an uninterrupted isolated import, through replacement.
E0 sets the version and representation to `ready` before CHUNK, allowing SQL
to expose stored chunk IDs. E1 can publish P1 rows before the last batch.
If isolation is uncertain, keep the grid and dead letter.
[E1 completion signal](processing_layers_design.md#51-gaps-and-frontiers) ·
[Legacy visibility and replay analysis](../analysis/split_oversized_chunks_analysis.md#incident-and-scope).

Re-run CHUNK on the immutable E0 inputs for the same version. Use committed
cuts as bounds so each new piece has one recorded parent. Take the recorded
`text_origin_at` from each intact row or piece's parent, including unknown.
For an unstamped pre-D140 intact row with both `text_origin_at` and
`reuse_identity_hash` unset, use E2's legacy fallback from the same version
(`source_modified_at`, then `published_at`, then unknown). Store the effective
date on rebuilt rows and use it in the date-sensitive Selection key under
§2, the E2 header and fresh claims' `asserted_at`.

Under the lineage lock, fence obsolete attempts and verify liveness, E1
incompleteness, continuous isolation and the failed work row for the exact
grid. Replace any recut private grid and remove its superseded embedding
stamps and P1 rows before reopening that same embed ledger row. Keep its
attempt count and error history. Hold the work row lock through replacement
and reset, then announce the committed work. If all cuts still fit, CHUNK
keeps the grid and reopens embed work through the same ledger transition.
Repeated requests for the failed attempt resume the selected work. E1 writes
verify liveness and a running, unexpired attempt under the same locks through
commit.

## 4. Rejection and retry

Classify confirmed deterministic per-input rejections before the capacity
retry code. Use terminal `embedding.input_too_large` for a confirmed size
failure and `embedding.input_rejected` for other confirmed input failures.
Bisect a confirmed input-rejected batch only to identify the failing input.
Keep bounded capacity retries for genuine throttling, ambiguous 429s and
transient outages.

An indexed reply must contain every zero-based request position exactly
once as an integer index. Validate every vector with the P1 dimension and
stored-vector checks before committing the batch. A malformed reply is a
typed invalid response with no successful stamps for that batch. Retain
ordinary accounting. A nonblank rejected input leaves the stage failed.
[Orchestration](orchestration_design.md#embed_chunk-durability-d80--minimum-contract).

## 5. Offline benchmark check

Re-pack the exact frozen LoCoMo Full-v45 and BEAM inputs at the benchmark
binding's provider-qualified ceiling of **at least 22,528 tokens**. Use the
saved E0 Markdown, blocks, sections, provenance, source facts and packing
and reuse inputs. Read expected rows and E2 completion state from the
pre-D162 stores. Record each baseline revision, backup receipt or snapshot
ID, representation inventory, input and baseline digests, and exact restore
and check commands in the implementation PR. Publish this check and the
provider qualification there.
[Fixture audit](../analysis/split_oversized_chunks_analysis.md#benchmark-sizing).

Compare `chunks` and `chunk_search` rows **column for column** in document
order, including nulls and generated values. Supply saved UUIDs and clock
values only where generated, and saved vectors only at the offline provider
seam after matching the exact inputs. Consume the allocation sequence
exactly. Use the candidate's packing and D80 prepare paths. For each recorded
D122 completion, including empty completions, derive `chunks.claimify_input_hash`
with the existing `claimify_input_hash` function. Use the candidate target's
Selection key and its ordered previous-eight producer keys under the fixture's
pinned reference policy. Leave the stamp SQL NULL for rows without a recorded
D122 completion.

Require equal row counts, equality of every value, zero splits, and zero model
calls. Compare the full rendered input with the pre-D162 header and body
recomposed and checked against the saved hash. For each target also compare the
ordered D122 producer IDs derived from each grid's numeric ordinals, and the
ordered typed D80 pairs E2 admits using its provenance checks and section-title
fallback.

Require the check to fail when a negative control changes only the numeric
ordinals, moves the location header into the indexed body, or drops only
the typed section title with the embedding text held fixed. Missing
fixtures or digest mismatches fail the check. Update the E1 acceptance YAML
with implementation. Cover a dense scalar fallback, a labelled provenance
gap, nonmonotone header size, same-span repacking across generations,
private recovery and refusal for complete or exposed grids, and terminal
rejection versus throttling.
[E1 acceptance check](../implementation_evals/eval_checks/e1_chunks_whole_blocks_no_overlap.yaml).

For the Rule 4 regression, ingest two dated versions of a notebook with
an oversized code cell in the prior-eight window of an intact Results chunk.
Edit only a later Appendix and hold the Results inputs fixed. Verify new
code piece IDs, empty E2 completions and equal Selection keys in window
order. Assert that Results reuses the same claim IDs and makes zero calls
for embedding, Selection and Claimify.

Also ingest two dated versions of one lineage in each direction, fitting to
split and split to fitting. In each pair, edit only a later Appendix. Keep
an intact parent chunk whose closest E2 neighbor lies across a nested
section, with identical local inputs, D80 input and ordered D122 producer
keys. Assert matching `reuse_identity_hash`, `extraction_input_hash` and
`claimify_input_hash`, the first version's `text_origin_at`, the same nonempty
claim IDs through `chunk_claims`, and zero paid embedding, Selection and
Claimify calls for the parent target. A split that changes the closest
neighbor's consumed text or passage ranges must change both E1 keys.

Repeat with a dense neighbor doubled so the first split piece exactly
matches the earlier window, including its clipped passages and origins.
Require the same reuse with unchanged D122 producers. Changing only an
ordered producer key re-keys Claimify independently. A matching window in a
fitting grid can inherit tagged keys from an earlier split grid and pass
them to the next fitting version.

Repack one representation with a parameter change after its original grid
completes. Check that the original ledger keys still resolve, the new grid
gets its own delivery, and readiness checks that grid's embed and required
claims work, including the empty case. Check immediate and deferred claims
handoffs. Recover a failed private grid with a newer generation present.
Check that recovery reopens the exact failed row and E2 uses that grid even
when its configured generation differs. An ambiguous legacy failure stays
dead-lettered.

In private recovery, recut a dated grid's neighbor while the intact target's
recorded origin predates the version. Check that the rebuilt Selection key,
E2 header and fresh `asserted_at` use that origin. Repeat with unstamped
pre-D140 rows using a source modification date, a publication date alone
and no date. Verify that an otherwise identical undated occurrence has a
different Selection key from a recovered dated occurrence.

With multiple grids in one representation, both section-link reads retain
legacy IDs and pick only the first intersecting chunk of the split grid.
