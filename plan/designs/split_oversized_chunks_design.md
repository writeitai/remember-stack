# Split oversized chunks in E1 (D162)

**Status** Proposed. The [incident audit](../analysis/split_oversized_chunks_analysis.md)
traces how a large block can pass the whitespace-token packer and exceed the
provider's per-input limit.

Apply D162 at CHUNK on first ingest, on each new version, and on replay of an
oversize `embed_chunk` dead letter before first E1 completion. READY here means
the passage grid completed E1. READY versions retain their cuts and handles,
including chunks above the qualified ceiling that the provider accepted.

## 1. Size contract and split points

Qualify the **full D80 input** (body, location header and separators) with
model framing and special tokens. Pin tokenizer code and data, framing, model,
dimensions, and the effective limits of the gateway and all reachable
endpoints. Prove full delivery to the model at the ceiling and fit for one
nonblank Unicode scalar with maximal header and framing. Qualify request
item count, framed tokens and serialized bytes including overhead. Use an
exact count or equivalent proof when an upper bound cannot prove fit. Start
hosted Qwen on `nebius/bf16` at **16,384 Qwen tokens**. NFC UTF-8 bytes
plus the template end token and route framing bound the Qwen count.

Use a closed endpoint allowlist with marketplace fallback disabled. Stock
self-host automatic or preference-only routing follows the existing path
until an operator activates a qualified binding. Verify the effective route
and fingerprint at setup, CHUNK and embedding dispatch. Drift blocks paid E1
work on the affected binding until requalified.
[Compose defaults](../../compose.yaml#L28-L36),
[OpenRouter routing](../../src/rememberstack/adapters/openrouter.py#L1001-L1034).

1. Run the existing packer (400 whitespace tokens, anchor modulus 24 and
   minimum gap 200 by default) and render every candidate with its original
   D80 facts and grid count. Share the snapshot with EMBED_CHUNK so a title
   change cannot invalidate fit. Fitting grids keep legacy bytes, spans,
   eligibility, keys and delivery identity. Replays keep matching IDs, fresh
   fitting rows use legacy allocation, and empty readings complete as before.
2. Refine only failing candidates, inside their original spans. Use the
   committed eligibility ranges, including labelled gaps the parser skips.
   Unlabelled text inherits the parent's eligibility. Attach whitespace-only
   ranges left (right at the start) with their original provenance. The
   resulting eligibility changes bound probes, anchors and separator
   ownership. Keep CRLF together and emit no blank pieces.
3. Prefer whole-block and recorded source seams (page, turn, cell or row).
   Inside a block use YAML document or sibling key/item seams outside block
   scalars, or prose sentence ends, then line ends, whitespace and Unicode
   scalars. Within other code and tables use line ends first. Pin these lexical
   rules. Form natural units at block and source seams and at the strongest
   structural tier within a block. Run D58's hash and minimum-gap rule on
   their exact bytes within each eligibility run, cutting before accepted
   anchors and resetting spacing only there.
4. Aim for the end of the target-th whitespace token or the next eligibility
   change, accepted anchor or candidate end, whichever comes first. From a
   block start, try complete-block runs ending by that goal, furthest first.
   Even a short fitting run takes priority. When cutting inside a block,
   stop at its next edge so the following block can fit whole. Probe
   prefixes of 1, 2, 4, … scalars from the first nonblank scalar, up to the
   goal or that edge. Stop at the first overflow or the goal and use the
   last proved fit. Take a reached required or block edge. Otherwise take the
   furthest fitting seam in the strongest tier, or the proved scalar cut.
   Attach separator whitespace left up to the next required edge or block.
5. Use multi-chunk D80 rules for split pieces. If a failing singleton fits
   whole under those rules, reserve a nonblank scalar for a second piece.
   Re-render with the final count and prove fit before committing. Dry-run,
   CHUNK and EMBED_CHUNK share the same fit check.
   [D80 count branches](../../src/rememberstack/core/embedding_input_policy.py#L249-L274).

## 2. Coordinates and reuse

Pieces concatenate to the exact candidate bytes. Store half-open Unicode
code-point offsets into the immutable E0 Markdown. Keep the parent section,
intersected block ordinals (the preceding block for a gap-only slice) and
source locators. Clip converter provenance to each consumed slice.

Only an actual split gives the CHUNK output a new packing stamp (base
parameters, split rules and envelope). Subsequent work names that stamp.
Split-grid IDs are UUIDv5 of representation, stamp and span. Complete
block runs with optional edge whitespace keep the ordered block-hash key.
Partial blocks and gaps hash exact bytes with a separate tag. E0, D80,
eligibility, text-origin, E2, reference and normalization policies keep their pins.

- **Passage vectors.** Match content and full D80 input hashes, policy, model
  and dimensions within the lineage on non-deleted versions at or before the
  target. Require proof of full delivery across all possible original routes
  if the endpoint is unknown. Verify interrupted P1-before-spine writes by
  exact chunk ID, even without the spine stamp. Stamp new verified vectors
  with their binding and reuse qualifying checkpoints.
- **Selection and Claimify.** Key split grids on the actual E2 slots (target,
  closest same-owner chunks on each side even across a child, explicit empty
  slots). Preserve legacy keys. For reuse across layouts in either direction,
  verify actual slot bytes, relative origins, eligibility, stable facts, dates
  and generations. Carry equivalent frozen context. For Claimify also check
  the same ordered Selection inputs and results from the preceding eight
  document chunks under D122, including empty results. Remap consumed and
  cited spans by relative offset and exact substring under D119. Reuse the
  claim IDs on a unique verified remap. Otherwise use ordinary E2.
  [Legacy keys](../../src/rememberstack/workers/e1.py#L734-L771),
  [consumed slots](../../src/rememberstack/spine/chunk_catalog.py#L373-L419).
- **Text origin.** Apply D140 to equivalent slots. A fragment can inherit an
  earlier parent's date if its bytes and actual same-owner context match
  uniquely as ordered slices with equivalent relative origins, eligibility,
  undated facts and generations. Match even when the parent was edited or
  offsets shifted. Use the earliest eligible date from non-deleted earlier
  versions, at or before the target's source date. Use D140's own-date or
  unknown fallback for unmatched text. Reuse recorded dates for equivalent
  slots on restart. Preserve the original span and date evidence with
  failed work so recovery order cannot change inheritance.

## 3. Recover the unpublished version at CHUNK

Dry-run the affected `embed_chunk` dead letters. Require a typed length error
or a measured overflow for the failed rendered input and binding. Verify the
failed job's representation and packing stamp against its committed Markdown,
blocks, section coordinates and D80 facts. Use the stored sidecar or verify
reproduction with its pinned blockizer. Block ambiguous replays and report the
reason. Check for prior E1 completion under the lineage lock, since E0 can
mark structure ready before E1 succeeds.

Keep the failed ledger row. Link a CHUNK restart for the same version, keyed
by failed work and qualified binding fingerprint, so it runs even when the
old CHUNK succeeded. Repeat replay resumes that recovery. An envelope change
before first E1 completion reserves a new CHUNK attempt from committed E0 and
fences the obsolete attempt in one transaction, including on a first ingest
with no earlier grid. Retain original date evidence and qualifying vectors.
Clear superseded unpublished rows and any partial legacy search projection
before first readiness. Readiness follows the current delivery.

Drain and fence incompatible workers on affected routes before activation.
Embedding, downstream workers and readiness load the named grid stamp. Check
the grid stamp, lease attempt and version liveness in each write or barrier
transaction under the existing lineage fence. Keep incomplete grids and new
batch checkpoints outside ready views and serving indexes. Insert the final
rows into the existing projection at ordinary E1 readiness and continue with
D143 at that version's existing position in the lineage.
[Ledger claim predicate](../../src/rememberstack/spine/work_ledger.py#L2176-L2192),
[E1 commit boundary](../../src/rememberstack/workers/e1.py#L535-L572).

## 4. Provider rejection and retry

Fill batches in grid order within every qualified limit. Preserve structured
length errors before retry classification and bisect deterministically
rejected batches to isolate singletons. Fail terminally with
`embedding.input_too_large` for proven size excess or `embedding.input_rejected`
for other confirmed per-input rejection. Report chunk, representation, size,
model, binding and status. Generic 429s, timeouts and outages retain bounded
capacity retries.

Indexed replies must match the exact set of zero-based request positions
once each, as non-Boolean integers. Run `require_p1_vector` on each vector
(dimension, stored float4 finiteness and nonzero norm). A malformed batch
records usage and fails as a typed invalid response before attesting any
vector. Validated subrequests can checkpoint independently. Only an empty
D80 body can skip, on either layout. These decoder and retry rules apply to
the E1 passage call. [P1 validator](../../src/rememberstack/ports/p1_index.py#L26-L49).

## 5. Benchmark admission and release checks

Keep retained LoCoMo and BEAM stores on pinned readers, schema and effective
query embedding bindings. Gate ingest, replay and reader changes on the exact
candidate worker, reader, schema and bindings passing on copies. Dry-run
frozen prepared inputs, including LoCoMo OCR and BEAM's retained rendering.
Compare ordered spans, bodies, eligibility and D80 hashes. Retained grids
keep their digests and IDs. Qualification makes zero ingestion model calls
and zero writes to the protected stores.

Compare complete ordered public tool responses under the recorded filters,
times and planner settings, including evidence, scores, statuses, pagination,
BEAM SQL values and LoCoMo read tools. Pin query preparation, decoder,
retries and routing. If any change, replay each route's captured raw replies
and errors through its public tools, including a legacy singleton at
index `1`. Use fixed vectors after query-path equivalence. A difference
blocks admission. Scorer smoke is separate.

LoCoMo requires **at least 22,528 Qwen tokens** on its exact protocol binding
(`nebius`, provider order unset) plus frozen input and grid parity. The
reported v45 singleton has 21,567 tokens before its header and splits at
16,384. The runner checks binding identity. An owner-approved comparison has
a separate run identity and preserves the published 94.09% and attestation.
[Fixture audit](../analysis/split_oversized_chunks_analysis.md#benchmark-fixture-audit),
[protocol binding](../../benchmarks/locomo/protocol.py#L127-L134),
[runner guard](../../benchmarks/locomo/runner.py#L1714-L1729).

| Focus | Implementation release check |
| --- | --- |
| Packing | Byte-identical fitting output. Cover header-only overflow, YAML, OCR tables, long turns, CRLF, scalar fallback and a skipped labelled HTML gap. Check exact spans, clipped origins, eligibility and nonblank pieces. With equal-tier cuts at 400 and 600 tokens and an anchor at 520, cut at 400 first, then respect the anchor. |
| Provider | Cover stock and drifted routing, inconclusive bounds, full delivery and framed minimum input, and individually fitting items that exceed a batch limit together. Exercise real 429s and typed rejections, shuffled, missing and duplicate positions, and a good vector beside a zero-norm one. Malformed batches attest nothing. Test malformed singletons in both layouts and a valid sibling checkpoint. |
| Recovery | Canary the two hosted dead letters after qualification. Exercise ambiguous evidence, an old sidecar, changed envelopes on first ingest and replay, an interrupted P1 stamp, stale attempts and a READY grid above the ceiling. Verify idempotence, liveness checks, one visible grid and reuse of qualifying checkpoints. |
| D56 and D140 | Test dated legacy → split and split → fitting with unchanged parent runs across a child and an oversized section elsewhere. Compare paid calls by stage, dates, spans and claim IDs. Keep the D122 window case. Edit a line in an earlier parent and check the inherited date and relative-date claims for an unchanged fragment beyond an anchor in both recovery orders. Append a dense block after a short fitting seam and require zero embedding calls for identical D80 input with full-delivery proof. Reject matching-hash vectors without that proof. |
