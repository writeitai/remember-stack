# Split oversized chunks in E1 (D162)

**Status** Proposed. The [incident audit](../analysis/split_oversized_chunks_analysis.md)
traces how a large block can pass the whitespace-token packer and exceed the
provider's per-input limit.

Apply D162 at CHUNK on a qualified binding for new versions and confirmed
oversize `embed_chunk` dead letters whose grids stayed private (§3). Preserve
cuts and handles for any grid that could already serve, including chunks above
the qualified ceiling that a provider accepted.

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
   minimum gap 200 by default). On recovery, use the verified committed cuts
   as bounds for further splits. Render every candidate with its D80 facts
   and grid count. Share the snapshot with EMBED_CHUNK so a title change
   cannot invalidate fit. Ordinary fitting grids keep legacy bytes, spans,
   eligibility, keys, ID allocation and delivery. A recovery that fits keeps
   its cuts and IDs and uses the new delivery (§3).
2. Refine only failing candidates, inside their original spans. Use the
   committed eligibility ranges, including labelled gaps the parser skips.
   Unlabelled text inherits the parent's eligibility. Attach whitespace-only
   ranges left (right at the start) with their original provenance. The
   resulting eligibility changes bound probes, anchors and separator
   ownership. Keep CRLF together and emit no blank pieces.
3. From a block start, try whole-block prefixes first, longest first up to
   the whitespace target, including a first block that alone exceeds the
   target. Respect the original D58 cuts and all eligibility changes. Take
   any proved fit, even a short one, before considering seams inside a block.
4. When a block or gap needs splitting, prefer recorded source seams (page,
   turn, cell or row). Then use YAML document or sibling key/item seams outside
   block scalars, or prose sentence ends, then line ends, whitespace and Unicode
   scalars. Within other code and tables use line ends first. Pin these lexical
   rules. Only within the interval that needs splitting, apply D58's hash and
   minimum-gap rule to the exact bytes of units from the strongest available
   tier. Cut before each accepted anchor and reset spacing there. Stop at
   the next block edge, eligibility change, accepted internal anchor,
   target-th whitespace token or candidate end, whichever comes first.
   Probe legal cuts after 1, 2, 4, … scalars from the first nonblank scalar up
   to that goal. Stop at the first overflow or the goal and keep the last
   passing cut. Within that span, try a reached required or block edge first,
   then seams by tier, furthest first in each tier. For every probe and seam,
   attach separator whitespace left up to the next required edge or block and
   run the full fit check on the resulting piece. D80 can render a larger
   header for a shorter body. If no preferred cut fits, use the last passing
   scalar cut.
5. Use multi-chunk D80 rules for split pieces. If a failing singleton fits
   whole under those rules, reserve a nonblank scalar for a second piece.
   Re-render with the final count and prove fit before committing. Dry-run,
   CHUNK and EMBED_CHUNK share the same fit check.
   [D80 count branches](../../src/rememberstack/core/embedding_input_policy.py#L249-L274).

## 2. Coordinates and reuse

Pieces concatenate to the exact candidate bytes. Store half-open Unicode
code-point offsets into the immutable E0 Markdown. Keep the parent section,
intersected block ordinals (the preceding block for a gap-only slice) and
source locators. Clip converter provenance to each consumed slice. On a split
grid, references and section history select at most one `first_chunk_ids` entry
per version and section. Pick the first published piece in document order
that intersects the section's first block.

Build Selection and Claimify passage labels per window on split grids. For an
eligible gap-only target or neighbour with no clipped block, label its exact
piece span and render the converter provenance clipped to that span. The
target label can ground a D122 card. Claimify still requires a Selection keep
for an origin citation.

Only an actual split gives the CHUNK output a new packing stamp (base
parameters, split rules and envelope). A restart without new cuts retains its
stamp. Grid-scoped work names that stamp and the selected delivery, including
claim embedding and both [D143 reconcile modes](processing_layers_design.md#42-the-claims-tail-and-the-two-reconcile-modes).
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
  verify slot bytes, passage slices, relative origins, eligibility, stable
  facts, dates and generations. Carry equivalent frozen context. Pair slots
  by role and remap frozen Selection cards through those slots. For Claimify
  also match the ordered Selection inputs and frozen results of the preceding
  eight document chunks under D122, including empty results. Replay admission
  from those results and require equivalent ordered cards. Pair supports by
  producer position, card ordinal and support order.

  Remap each citation through its corresponding local slot or admitted card
  support. The next same-owner slot can sit beyond several pieces of a child
  section. Use D119's relative offsets and exact substring checks. Require one
  distinct destination per span. The origin must still overlap a Selection
  keep on the target. Reuse the claim IDs with provenance resolved in the new
  representation. Use ordinary E2 if verification fails.
  [Legacy keys](../../src/rememberstack/workers/e1.py#L734-L771),
  [consumed slots](../../src/rememberstack/spine/chunk_catalog.py#L373-L419).
- **Text origin.** On first packing a version, apply D140 to equivalent slots.
  A fragment can inherit an earlier parent's date if its bytes and actual
  same-owner context match uniquely as ordered slices with equivalent relative
  origins, eligibility, undated facts and generations. Match even when the
  parent was edited or offsets shifted. Use the earliest eligible date from
  non-deleted earlier versions at or before the target's source date. Use D140's
  own-date or unknown fallback for unmatched text. A date donor can have
  different paid inputs or no extraction result. Apply the separate reuse
  checks above for vectors, Selection and Claimify. On same-version recovery,
  retain the verified parent spans and copy each parent's recorded
  `text_origin_at` to its pieces, including a recorded unknown. This holds
  when the E2 slots change or the earlier donor was deleted. Preserve
  committed cuts so a piece always has one parent.

  If recovery recuts a grid, retain the first committed grid's date evidence
  once. Keep each original parent's ordinal, date, date-free identity and
  its target and same-owner neighbour spans (including empty slots),
  eligibility, undated facts and generations. Read bytes and clipped
  provenance from the immutable E0 representation. Switch both hash and
  slice lookups to this evidence in the same lineage transaction that removes
  the original rows. Use the same donor set through repeated recovery and
  after publication. The recovered pieces carry their dates into E2 and
  D140 uses the retained rows for matching later versions. A restart that
  keeps the original grid can use its chunk rows.

## 3. Recover the unpublished version at CHUNK

Dry-run the affected `embed_chunk` dead letters. Require a typed length error
or a measured overflow for the failed input and binding. Verify the failed
job's representation and packing stamp against committed Markdown, blocks,
section coordinates and D80 facts. Use the stored sidecar or verify
reproduction with its pinned blockizer. Block ambiguous replays and report
the reason.

Under the lineage lock, verify that E1 never completed and the grid stayed
private from the first chunk commit through recovery. D162's pending state
records that protection. For a legacy grid, require a recorded isolation
boundary covering the entire interval, such as an offline import with SQL
and service readers fenced. E0 readiness exposes legacy IDs through SQL
even with an empty P1 projection, and successful legacy batches can enter
search before the job ends. Use
ordinary same-grid `EMBED_CHUNK` replay for exposed or unverified legacy grids
if a binding can accept the original input. Otherwise retain the dead letter.
[Public chunk view](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L719),
[legacy batch commit](../../src/rememberstack/workers/e1.py#L535-L572).

Retain the failed ledger row. Select a CHUNK restart for the same version,
keyed by superseded work and qualified binding fingerprint. Each restart gets
an EMBED_CHUNK component version derived from that restart key, including
when the wider envelope fits the grid with zero cuts. Repeated replay resumes
the selected delivery. On an envelope change before publication, reserve the
new delivery and fence the obsolete one in the same lineage transaction, even
if CHUNK has yet to commit a grid. Record the selected delivery and its packing
stamp for the representation. Readiness and D143 source completion, claims
admission and backfill resolve that exact delivery and grid on the original
version, including when no chunks exist. Keep qualifying checkpoints and
the origin evidence from §2 before clearing superseded private rows.
[Ledger key](../../src/rememberstack/spine/work_ledger.py#L1996-L2023),
[D143 lookup](../../src/rememberstack/spine/processing_layers.py#L1268-L1275).

**Checkpoints and forget.** Store staged vectors and prepared source and
D80 input data off-index in PostgreSQL. Store retained origin records with
the representation, independently of any delivery. Index both by owner
`(deployment_id, doc_id)`. In every write or barrier transaction, check
lineage and version liveness, selected delivery, packing stamp and lease
attempt under the same lineage lock as D159 hide. Discard late output
after the hide and exclude hidden donors immediately from date lookups.
The [D159 sweep](archive_and_forget_design.md#33-the-sweep) deletes both
sets by document, including published representations and deliveries
whose chunk rows are already gone.

**First publication.** Activate the gate with compatible readers after
draining and fencing incompatible workers. Enroll each new D162 representation
as pending for passages in the same lineage transaction that first makes it
E0-ready, before CHUNK is enqueued. Enrollment is idempotent. It exists even
before a delivery or packing stamp is recorded. CHUNK commits the selected
delivery and stamp with the grid, including an empty grid.
Waiting, failure and delivery replacement preserve pending status.

Withhold pending passages from current and all-version SQL views, body
fetches, passage search, adjacency and section links. Scoped passage reads use
the same enrollment for [D140 pending disclosure](effective_time_and_section_references_design.md#37-results-carry-their-period-and-pending-lineages).
E0 readiness governs document and section metadata. For a proven-private
legacy grid, adopt the gate and withdraw partial P1 rows before ending
isolation. Only the selected delivery's successful E1 completion clears
pending. In that transaction install the final P1 projection, publish the
grid, delete delivery checkpoints and succeed the ledger row, including on
the empty-grid path. Keep the representation's origin records for D140.
D143 continues from that delivery.

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

LoCoMo and BEAM use their pinned paths until a candidate passes on copies.
Record a non-secret fingerprint with the protected run for the worker and
reader builds and component generations, applied schema, and effective
passage and query embedding bindings that passed. The embedding bindings
include the actual gateway URL without credentials, model and dimensions,
qualified endpoints, routing and fallback, zero-data-retention and
data-collection settings, tokenizer, framing, and all qualified limits.
Pin any request, decoder and retry settings used in parity.

Verify the complete identity of the running deployment before protected
ingest, replay or reads, including at BEAM's supplied API URL. Carry the
expected identity into queued work and check it again at CHUNK and
EMBED_CHUNK execution. Each read checks the serving reader, schema and
query adapter at execution. Use the checked settings and schema for the
operation. Use the pinned deployment when a candidate identity is
missing or differs. Suspend a candidate-bound run on drift until its
recorded identity is restored.

For parity with LoCoMo's published 94.09% run, replay its saved tool traces
using its recorded reader and copies of its attested retained v45 stores.
Current Full-v47 execution uses its own profile.
[LoCoMo baselines](locomo_benchmark_design.md#2-fixed-protocol).

Dry-run frozen inputs including LoCoMo OCR and BEAM's retained rendering.
Compare ordered spans, bodies, eligibility and D80 hashes. Check retained
grid digests and IDs.

For an E1 change, replay captured raw provider replies and errors at the
transport boundary through both workers on copies. Bind replies to the
recorded embedding requests and run the adapters, retries and persistence.
Require equal delivery outcomes (success, retry, skip or dead letter) and
stored float4 passage vectors for matching spans and D80 inputs. Include
a singleton with a valid vector at index `1` on a fitting grid. If the candidate rejects a reply the pinned path
accepts, keep the protected ingest on the pinned path. Qualification uses
zero ingestion model calls and zero writes to protected stores.

Replay the public read tools, including BEAM SQL and LoCoMo reads, with the
recorded filters, planner settings and frozen application and SQL evaluation
clocks, including effective and belief times. In SQL responses replace
`request_id`, `elapsed_ms`, `execution_started_at` and `pg_snapshot_at` with
fixed placeholders for comparison, preserving nulls. Compare the full ordered
responses, including values, evidence, scores, applied times, statuses,
limits, truncation and pagination. Require a reader to pass against itself
before comparing builds. Pin query preparation, decoder, retries and routing.
If any change, replay each route's captured raw replies and errors through its
public tools, including the singleton at index `1`. Use fixed vectors after
query-path equivalence. A difference blocks admission. Scorer smoke is separate.

LoCoMo requires **at least 22,528 Qwen tokens** on its exact protocol binding
(`nebius`, provider order unset) plus frozen input and grid parity. The
reported v45 singleton has 21,567 tokens before its header and splits at
16,384. An owner-approved comparison has a separate run identity and
preserves the published 94.09% and attestation.
[Fixture audit](../analysis/split_oversized_chunks_analysis.md#benchmark-fixture-audit),
[protocol binding](../../benchmarks/locomo/protocol.py#L127-L134).

As part of implementation, update the
[E1 acceptance check](../implementation_evals/eval_checks/e1_chunks_whole_blocks_no_overlap.yaml)
to verify D58 packing and D162 splits. Require section-bounded,
non-overlapping pieces that concatenate to the exact candidate bytes, and
byte-identical grids when the original packing fits.

| Focus | Implementation release check |
| --- | --- |
| Packing | Byte-identical fitting output. Cover header-only overflow, YAML, OCR tables, long turns, CRLF and a skipped labelled HTML gap. Check exact spans, clipped origins, eligibility and nonblank pieces. With equal-tier cuts at 400 and 600 tokens and an anchor at 520, cut at 400 first, then respect the anchor. Reject a preferred line seam whose D80-rendered request exceeds the byte limit even though a longer scalar probe fits. Take the proved scalar cut when every preferred seam fails. |
| Gap-only evidence | Split an eligible HTML gap beside a neighbour with a clipped block. Require a published D122 card citing the exact gap-only target span with its clipped converter provenance. |
| Benchmark binding | Hold model and provider fixed and change only the gateway URL. Repeat with only the retention policy changed. Keep protected ingest, replay and reads on the pinned path, and refuse a mismatched BEAM API URL before retrieval. Exercise missing identity and worker, reader and schema drift. If drift follows candidate enqueue, stop before candidate execution. |
| Provider | Cover stock and drifted routing, inconclusive bounds, full delivery and framed minimum input, and individually fitting items that exceed a batch limit together. Exercise real 429s and typed rejections, shuffled, missing and duplicate positions, and a good vector beside a zero-norm one. Malformed batches attest nothing. Test malformed singletons in both layouts and a valid sibling checkpoint. |
| Publication | Dead-letter legacy E1 after a successful batch. Observe its handles through SQL and search, refuse CHUNK recovery, and verify ordinary embed replay preserves them. Check SQL, search and body fetches, adjacency and section links before and after D162 publication. Split a section's first block and check that references and history each return its first piece only. |
| Scoped passages | Search by document ID with an E0-ready in-force D162 version and a published predecessor. Check before CHUNK enqueue, after CHUNK dead-letters without writing rows and after an EMBED_CHUNK dead letter. Each yields `boundary` and names the document in `scope_pending`. On a published empty grid with an exhausted search, require `known_empty`. Publish between nomination and the pending check and verify both use the original read snapshot. |
| Claims tails | Ingest a fresh document that splits, with facts disabled and with the full pipeline. After each tail finishes, find a new claim through semantic claim search and verify it remains current testimony with no `reextracted` transition. |
| Recovery | Audit the two hosted dead letters and canary any with verified privacy. Exercise ambiguous evidence, an old sidecar, changed envelopes on first ingest and replay, an interrupted P1 stamp, stale attempts and a published grid above the ceiling. Widen the envelope for a private legacy grid so it fits without cuts. Replay twice and check that one distinct embed delivery succeeds, IDs stay stable, and readiness and D143 advance on that delivery. |
| Date lookup across publication | Narrow a private January 1 grid in recovery. Give the February 1 version an edit in another section and a wider ceiling so its unchanged piece and context match the original parent, with the piece spanning multiple recovered pieces. Pack February before January recovery and after January publication on independent copies. Require January 1 as `text_origin_at` in both orders. |
| Forget | Checkpoint a partial batch and dead-letter the delivery. Forget, then release an obsolete attempt's reply after the hide. Also forget a document with a published recut grid. Verify immediate invisibility to readers and date lookup, rejection of the late write, and zero staged content, retained origin records or P1 rows after repeated sweeps. |
| D56 and D140 | Test dated legacy → split and split → fitting within one lineage with stable names and untouched sections on both sides of an edited multi-chunk section. Check paid calls by stage, identities, dates, spans and claim IDs under Rule 4, including the D122 window case. Require a verified fragment to inherit the date of a donor with different inputs or no extraction, then apply normal paid reuse checks. Check an unchanged fragment beyond an anchor after an edit in both recovery orders. Delete the January 1 version after B inherits its date and before B's oversize replay. Verify that B's pieces keep January 1 and E2 resolves “tomorrow” to January 2. Append the 17,000-digit block to the fitting paragraph with a qualifying internal anchor. Require zero embedding calls for that intact paragraph given the same D80 input and full-delivery proof. Reject matching-hash vectors without that proof. |
| Cross-layout citations | Hold the local slots and preceding eight Selection inputs and results fixed while a split child shifts ordinals. Exercise a secondary citation to the next same-owner chunk across the child and an admitted card support outside the old ordinal window. In both layout directions require the same claim IDs, exact remapped spans and zero Claimify calls for matching targets. |
