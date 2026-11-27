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
framing. If all fit, use the existing row assembly, allocation, work keys
and reuse path. Every stored field has its legacy value given the same
inputs and allocations. The base chunker, embedding and extraction
generations stay in use.

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
grid. Section references and history use the first piece that intersects
the section's first block, at most one per version and section.

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
Recompute intact-chunk reuse keys from their actual inputs and windows,
including any split neighbours consumed by E2.
A new split piece takes the version's D140 source date or unknown, with its
date-free lookup key unset. In CHUNK recovery it keeps its same-version
parent's recorded date, including unknown. Ordinary retries use completions
of the same row.
[Existing E1 reuse rules](e1_chunks_design.md#7-reuse-mechanics-d56-bound-here-amendments-a1a3).

## 3. Recover through CHUNK while the grid is unpublished

A grid completes E1 when its `embed_chunk` ledger work succeeds for that
representation and packing generation. E1-complete and previously readable
grids keep their cuts and IDs. For an incomplete grid with an `embed_chunk`
dead letter, verify the typed size error or measure that the failed input
exceeds its qualified ceiling. Require separate evidence of continuous reader
isolation through replacement, such as an uninterrupted isolated import.
E0 sets the version and representation to `ready` before CHUNK, allowing SQL
to expose stored chunk IDs. E1 can publish P1 rows before the last batch.
If isolation is uncertain, keep the grid and dead letter.
[E1 completion signal](processing_layers_design.md#51-gaps-and-frontiers) ·
[Legacy visibility and replay analysis](../analysis/split_oversized_chunks_analysis.md#incident-and-scope).

Re-run CHUNK on the immutable E0 inputs for the same version. Use committed
cuts as bounds so each new piece has one recorded parent. Under the lineage
lock, fence obsolete attempts and verify liveness, E1 incompleteness,
continuous isolation and the failed work row. Replace any recut private grid
and remove its superseded embedding stamps and P1 rows before reopening that
same embed ledger row. Keep its attempt count and error history. Hold the work
row lock through the replacement and reset, then announce the committed work.
If all cuts still fit, CHUNK keeps the grid and reopens embed work through
the same ledger transition. Key recovery to the failed attempt so repeated requests resume
the selected work. E1 writes verify liveness and a running, unexpired
attempt under the same locks through commit.

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
