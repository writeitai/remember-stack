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
   eligibility, keys, ID allocation and embed-delivery identity. A recovery
   that fits keeps its cuts and IDs and uses the new delivery (§3).
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
block runs with optional edge whitespace keep the ordered block hashes in
`chunk_content_hash`. Partial blocks and gaps hash exact bytes with a separate
tag. Use the existing E0, D80, eligibility, D140, E2, D122 and normalization
policy generations.

Split grids key both the date-free `reuse_identity_hash` and the dated
`extraction_input_hash` on exact E2 source slots. These are the target and
closest same-owner chunks on either side, even across a child. Use a tagged
digest of each slot's exact UTF-8 span, including parser-skipped gaps and
separator whitespace, with roles and an explicit absent-slot marker. Keep
provenance, eligibility, stable facts and generations in the keys. Fitting
grids use legacy keys. When either grid is split, both date inheritance and
extraction reuse require equal slot bytes by role. Matching split-slot
digests establish byte equality for hash matches. For cross-layout and
fragment matches, verify the exact slices in the immutable E0 Markdown.

- **Passage vectors.** Search non-deleted versions of the same lineage at or
  before the target. Require matching full D80 input hashes, policy, model and
  dimensions. Look for a matching content hash first. If that lookup yields
  no verified donor, use the D80 input hash to find candidates across
  whole-block and fragment hashes in either direction. Require byte-identical
  slices from their immutable E0 Markdown. Apply the same donor proofs to
  both paths. Publish the target's own content hash and prepared projection
  with the verified vector.

  Prove full delivery on the original route, or on all possible
  original routes if the endpoint is unknown. Duplicate reply indices can
  associate valid vectors with the wrong inputs. Verify the original
  request and raw reply under §4 and match the position's input hash and
  float4 vector to the row, or use an attestation of those checks.
  Require both proofs for P1-only checkpoints, matched by exact chunk ID.
  Keep the verified binding and mapping with new checkpoints, published
  vectors and any copies made on reuse.
  Continue to older donors if a newer one fails source or proof verification.
  The first D162 delivery of a fitting edit to a pre-D162 E1 READY version
  can re-embed unchanged passages that have no verified donor. A legacy
  vector and expected input hash alone cannot prove their mapping because
  the decoder discarded the reply indices. The first successful
  re-embedding supplies a verified donor for later matching versions.
  [Legacy decoder](../../src/rememberstack/adapters/openrouter.py#L1052-L1077),
  [legacy E1 stamps](../../src/rememberstack/workers/e1.py#L502-L572).
- **Selection and Claimify.** For each stage, search earlier non-deleted
  versions of the same live lineage for committed results, including empty
  results. Use stored stage hashes between grids of the same layout. Across
  fitting and split layouts, use one indexed digest of the exact E2 slot bytes
  by role, including absent slots. Derive it from each grid's committed spans
  and E0 Markdown. Fill missing digests by representation on first
  mixed-layout lookup so earlier grids participate. Both stages use this
  digest to find candidates. Verify each completion against its own layout's
  key under the current policies and compare the underlying inputs across
  layouts.

  Visit eligible donors from both lookups in descending version number, then
  ascending chunk ordinal and chunk ID. Continue past failed verification or
  remapping and use the first donor that passes. Run ordinary E2 only after
  exhausting the candidates for that stage.

  Whenever either grid is split, including with the same packing stamp,
  compare the passage catalogues E2 builds from each grid's committed
  blocks. In label order, match each range's slot role and its start and end
  relative to that slot. An edit outside a slot can change its catalogue by
  changing the block that encloses it.
  Also verify passage text, relative origins, eligibility, stable facts,
  dates and generations. Compare the ordered typed D80 `(kind, text)` pairs
  admitted by E2 for the target, using its provenance checks and legacy
  fallback. E2 can receive a changed section title even when the bounded
  embedding header omits it. Carry equivalent frozen context and remap
  Selection cards through the paired slots to matching catalogue ranges.

  For Claimify, match the target's frozen Selection result and use its keep
  ranges in the catalogue. Also match each range's origin eligibility.
  Match the preceding eight producers' Selection inputs, including their
  catalogues and typed D80 pairs, and their frozen results in D122 order,
  including empty results.
  Replay admission and require equivalent ordered cards. Pair their
  supports by producer position, card ordinal and support order.

  Remap each citation through its corresponding local slot or admitted card
  support. The next same-owner slot can sit beyond several pieces of a child
  section. Use D119's relative offsets and exact substring checks. Require a
  distinct catalogue range for each remapped span. The origin must still
  overlap a Selection keep on the target. Reuse the claim IDs with provenance
  resolved in the new representation.
  [Legacy header facts and eligibility marker](../../src/rememberstack/workers/e1.py#L768-L791),
  [dated key](../../src/rememberstack/workers/e1.py#L678-L731),
  [date-free key](../../src/rememberstack/workers/e1.py#L656-L675),
  [Claimify key](../../src/rememberstack/core/selection_references.py#L70-L86),
  [consumed slots](../../src/rememberstack/spine/chunk_catalog.py#L373-L419),
  [E2 location inputs and grounding](../../src/rememberstack/workers/e2.py#L1596-L1737).
- **Text origin.** On first packing a version, apply D140 to equivalent slots.
  A fragment can inherit an earlier parent's date if all slots match uniquely
  as ordered slices with equivalent relative origins, eligibility, undated
  facts and generations. Match even when the parent was edited or offsets
  shifted. Use the earliest eligible date from non-deleted earlier versions
  at or before the target's source date. Use D140's
  own-date or unknown fallback for unmatched text. A date donor can have
  different paid inputs, including different typed D80 pairs, or no extraction
  result. Apply the separate reuse checks above for vectors, Selection and
  Claimify. On same-version recovery, retain the verified parent spans and
  copy each parent's recorded `text_origin_at` to its pieces, including a
  recorded unknown. This holds when the E2 slots change or the earlier donor
  was deleted. Preserve committed cuts so a piece always has one parent.

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
`(deployment_id, doc_id)`. Every worker write or barrier transaction takes
the D159 lineage lock and locks its `processing_state` row `FOR UPDATE`.
Keep D143's layer-before-lineage lock order where the layer fence applies.
Under the locks, check lineage and version liveness, the selected delivery
and packing stamp, and that the ledger row still holds the running,
unexpired attempt. Hold both row locks through commit so the reaper cannot
reclaim the attempt between validation and the write. Discard stale output
and exclude hidden donors immediately from date lookups.
[Lease reaper](../../src/rememberstack/spine/work_ledger.py#L2290-L2322).
The [D159 sweep](archive_and_forget_design.md#33-the-sweep) deletes both
sets by document, including published representations and deliveries
whose chunk rows are already gone.

**First publication.** Apply the additive migration while the previous release
serves ordinary SQL and saved queries with enrollment off. Drain its readers
before incoming `setup` publishes the new manifest and transitions saved
queries. Verify that compatible readers enforce the passage gate, and drain
and fence incompatible workers before activating enrollment.
[D160 and D161 migration rules](packaging_distribution_design.md#61-migration-compatibility),
[manifest cutover](open_query_space_design.md#5-saved-query-registry).

Enroll each new D162 representation as pending for passages in the same
lineage transaction that first makes it E0-ready, before CHUNK is enqueued.
Enrollment is idempotent. It exists even before a delivery or packing stamp
is recorded. CHUNK commits the selected delivery and stamp with the grid,
including an empty grid. Waiting, failure and delivery replacement preserve
pending status.

Withhold pending passages from current and all-version SQL views, body
fetches, passage search, adjacency and section links. The `section_history`
lookup reads chunk rows directly and must use the same gate for
`first_chunk_ids` as section references. Scoped passage reads use the same
enrollment for [D140 pending disclosure](effective_time_and_section_references_design.md#37-results-carry-their-period-and-pending-lineages).
E0 readiness governs document and section metadata. For a proven-private
legacy grid, adopt the gate and withdraw partial P1 rows before ending
isolation. Only the selected delivery's successful E1 completion clears
pending. In that transaction install the final P1 projection, publish the
grid, delete delivery checkpoints and succeed the ledger row, including on
the empty-grid path. Keep the representation's origin records for D140.
D143 continues from that delivery.

Record and rehearse the activation and rollback procedures with the
implementation. Before activation, roll back binaries on the expanded
schema. If setup published the incoming hash, drain its readers and
republish the previous hash with the registry's atomic revalidation
transition and shipped-example seed. Run those registry steps directly
because the previous release's setup tries to resolve the newer Alembic
revision first. After activation, enforce the gate throughout recovery.
Returning to incompatible binaries requires a verified pre-activation
restore under the standard restore and forget rules.
[Restore rules](packaging_distribution_design.md#6-releases-upgrades-portability).

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

## 5. Offline benchmark and release checks

Qualify the hosted route under §1. Run the offline grid check at the exact
benchmark binding's qualified ceiling of **at least 22,528 Qwen tokens**.
LoCoMo's binding is Qwen3-Embedding-8B on OpenRouter with `nebius` and
provider order unset. The v45 audit reports a 21,567-token OCR singleton
before its header. It would split at the hosted starting ceiling of 16,384.
[Fixture audit](../analysis/split_oversized_chunks_analysis.md#benchmark-fixture-audit),
[LoCoMo binding](../../benchmarks/locomo/protocol.py#L127-L134).

Freeze the complete representation inventory from the pre-D162 stores.
Identify the LoCoMo v45 backups by ingestion revision and receipt. Identify
the retained BEAM 100K/1 store by ingestion revision and snapshot ID, and
record its upstream data revision. In the implementation PR, record those
identifiers, document, version and representation IDs with stored chunk
counts, SHA-256 digests of the frozen inputs and expected grids, and the
exact restore or extraction and check commands. Publish the offline
comparison, hosted qualification and rollout rehearsal results there.
[LoCoMo restore workflow](../../benchmarks/locomo/sharding/README.md#4-restore-one-conversation).

Take expected rows and embedding inputs from the pre-D162 grids. Keep
the normalized body and optional D80 header as separate expected fields.
Recompose the full input with D80's separator when a header is present
and verify the stored embedding hash. Retain each passage's
`location_facts_json` and the joined section title E2 uses as a fallback.
Run the D162 packer and renderer on the saved E0 Markdown, blocks,
sections, provenance, eligibility, source facts, packing parameters,
generations and text-origin dates. Use the candidate grid's sections and
chunk count to build its passage facts and prepare the payload E1 would
publish. Inject the legacy UUIDv4 sequence in allocation order only at the
fitting-grid allocator. Require it to be consumed exactly.
[Legacy row assembly](../../src/rememberstack/workers/e1.py#L678-L731),
[E1 location facts](../../src/rememberstack/workers/e1.py#L628-L653),
[D80 input assembly](../../src/rememberstack/core/embedding_input_policy.py#L218-L230),
[E1 payload](../../src/rememberstack/workers/e1.py#L551-L567).

Require the same chunk count and compare the grids field by field in
ordinal order. Check IDs, exact numeric `ordinal` values, the owning
`section_id`, block and character bounds, source bytes, token counts,
eligibility and its policy stamp, text origin, packing stamp and CHUNK
hashes. Compare the deterministic projection fields, including section
role, source kind and shape, D80 policy and model stamps, the normalized
indexed body and the optional `location_header` as separate fields.
Require byte-identical full D80 inputs and matching embedding hashes.
Compare the ordered `(kind, text)` pairs E2 admits using its provenance
checks and each grid's section title for the legacy fallback. D122 uses
numeric ordinals to choose producers, and body fetches return the two
text fields separately.
[D122 producer window](../../src/rememberstack/workers/extraction_references.py#L56-L63) ·
[Body fetch](../../src/rememberstack/surfaces/query_sandbox/nomination.py#L811-L832) ·
[E2 location validation and fallback](../../src/rememberstack/workers/e2.py#L1691-L1737).

Require the same comparison to fail on each negative control. Multiply
the ordinals by ten in a multi-chunk grid. Move a nonempty location header
and its separator into the indexed body and clear the separate header.
Remove only `section_title` from a prepared payload with other valid pairs.
Keep row order, source spans, full D80 inputs and hashes fixed in each
control. Require zero splits and zero model calls on the baseline check.
Missing fixtures, digest mismatches or differences fail the release check.

Fitting grids enrolled under §3 are pending for passage reads until first
E1 publication. The publication and scoped-passage checks below exercise
that transition with a fitting grid and a published control. Update and
verify the SQL manifest through the existing query-space checks. Changing
the chunk view definitions changes `surface_manifest_hash` and triggers
the usual saved-query revalidation.
[Read-path audit](../analysis/split_oversized_chunks_analysis.md#read-behavior-for-fitting-grids),
[query-space manifest](open_query_space_design.md#6-discovery-manifest-and-benchmark-identity).

The [E1 acceptance check](../implementation_evals/eval_checks/e1_chunks_whole_blocks_no_overlap.yaml)
must judge the D58 anchored whitespace packer and D162's refinement of only
over-ceiling candidates. Require section-bounded, non-overlapping pieces
that concatenate to the exact candidate bytes, with provenance and the
content hashes from §2. Require byte-identical grids, eligibility and keys
when the original packing fits. Update the YAML judge in the implementation PR.

| Focus | Implementation release check |
| --- | --- |
| Packing | Byte-identical fitting output. Cover header-only overflow, YAML, OCR tables, long turns, CRLF and a skipped labelled HTML gap. Check exact spans, clipped origins, eligibility and nonblank pieces. With equal-tier cuts at 400 and 600 tokens and an anchor at 520, cut at 400 first, then respect the anchor. Reject a preferred line seam whose D80-rendered request exceeds the byte limit even though a longer scalar probe fits. Take the proved scalar cut when every preferred seam fails. |
| Gap-only evidence | Split an eligible HTML gap beside a neighbour with a clipped block. Require a published D122 card citing the exact gap-only target span with its clipped converter provenance. |
| Provider | Cover stock and drifted routing, inconclusive bounds, full delivery and framed minimum input, and individually fitting items that exceed a batch limit together. Exercise real 429s and typed rejections, shuffled, missing and duplicate positions, and a good vector beside a zero-norm one. Malformed batches attest nothing. Test malformed singletons in both layouts and a valid sibling checkpoint. |
| Publication | Check current and all-version SQL, passage search, body fetches, adjacency, section references and history with a fitting D162 grid and a published legacy control. Checkpoint a batch and verify that the fitting grid's passages and links are absent. Verify that they appear together with their committed IDs at first publication and that the control is readable throughout. Dead-letter legacy E1 after a successful batch. Observe its handles through SQL and search, refuse CHUNK recovery, and verify ordinary embed replay preserves them. Split a section's first block and check that references and history each return its first piece only. |
| Rollout | Under D160, compare previous-release writes, ordinary SQL and active saved queries before and after schema expansion with enrollment off. Force a DDL lock timeout and verify D161 retry from the last committed revision. After old readers drain, verify setup publishes the new hash and moves active saved queries on the old hash to pending revalidation in one commit. Verify the passage gate before enrollment and reject an incompatible worker's late write. Rehearse both §3 rollbacks. Verify previous-release SQL and revalidated saved queries after manifest rollback on the expanded schema and after restoring the pre-activation snapshot. |
| Publication races | Pause after checkpoint validation until the lease expires and verify the reaper skips the locked row. Reclaim before locked validation on a second run and verify the old attempt writes nothing. Interrupt first publication before commit. In `section_history`, keep published `first_chunk_ids`, withhold pending IDs and return one first piece after resume. |
| Scoped passages | Scope the fitting-grid fixture to its in-force D162 version by document ID. Check before CHUNK enqueue, after CHUNK dead-letters without writing rows and after an EMBED_CHUNK dead letter. Each yields `boundary` and names the document in `scope_pending`. Check that publication clears pending. On a published empty grid with an exhausted search, require `known_empty`. Publish between nomination and the pending check and verify both use the original read snapshot. |
| Claims tails | Ingest a fresh document that splits, with facts disabled and with the full pipeline. After each tail finishes, find a new claim through semantic claim search and verify it remains current testimony with no `reextracted` transition. |
| Recovery | Audit the two hosted dead letters and canary any with verified privacy. Exercise ambiguous evidence, an old sidecar, changed envelopes on first ingest and replay, an interrupted P1 stamp, stale attempts and a published grid above the ceiling. Widen the envelope for a private legacy grid so it fits without cuts. Replay twice and check that one distinct embed delivery succeeds, IDs stay stable, and readiness and D143 advance on that delivery. |
| Legacy vector recovery | Let a two-input legacy batch accept duplicate index `0` replies before a later batch dead-letters on oversize. Reject both disputed vectors despite matching hashes and full delivery, including with only a P1 row. Supply a separate verified donor for one passage and a verified P1-only checkpoint as a control. Re-embed only inputs without a verified donor. |
| Fitting edit with legacy vectors | Edit a pre-D162 E1 READY version under an active D162 binding and keep the grid fitting. Count provider calls and require new embeddings for unchanged passages with no verified match. An older verified donor must prevent a call even when a newer matching row lacks proof. A subsequent edit reuses newly verified matching vectors. |
| Whole block becomes a fragment | At a 16,384-token Qwen ceiling, embed a standalone, body-only 8,192-digit paragraph and retain its verified vector. Append 9,000 digits without a block break in a new version of the same lineage. Assert that the first split piece has the original slice and D80 hash and a different content hash. Require zero embedding calls for the prefix and embed the new suffix. |
| Date lookup across publication | Narrow a private January 1 grid in recovery. Give the February 1 version an edit in another section and a wider ceiling so its unchanged piece and context match the original parent, with the piece spanning multiple recovered pieces. Pack February before January recovery and after January publication on independent copies. Require January 1 as `text_origin_at` in both orders. |
| Forget | Checkpoint a partial batch and dead-letter the delivery. Forget, then release an obsolete attempt's reply after the hide. Also forget a document with a published recut grid. Verify immediate invisibility to readers and date lookup, rejection of the late write, and zero staged content, retained origin records or P1 rows after repeated sweeps. |
| D56 and D140 | Test dated legacy → split and split → fitting within one lineage with stable names and untouched sections on both sides of an edited multi-chunk section. Check paid calls by stage, identities, dates, spans and claim IDs under Rule 4, including the D122 window case. Require a verified fragment to inherit the date of a donor with different inputs or no extraction, then apply normal paid reuse checks. Check an unchanged fragment beyond an anchor after an edit in both recovery orders. Delete the January 1 version after B inherits its date and before B's oversize replay. Verify that B's pieces keep January 1 and E2 resolves “tomorrow” to January 2. Append the 17,000-digit block to the fitting paragraph with a qualifying internal anchor. Require zero embedding calls for that intact paragraph with a matching D80 input and verified donor. |
| Split-to-split gap edit | Pack January 1 and February 1 versions of one lineage into split grids with stable names and cuts. Change `Friday` to `Monday` in a parser-skipped HTML gap within a whole-block prefix, holding block and relative origin hashes fixed. Require the changed target and consumers of that slot to take February 1 and miss Selection reuse. Outside the affected slots and D122 window, require inherited dates, reused claim IDs and zero new embedding, Selection or Claimify calls. |
| Split-to-split catalogue edit | Remove fence markers outside the three consumed slots and hold the packing stamp, slot bytes and relative origin hashes equal. Require the matching D140 date to carry over. Reject Selection and Claimify reuse where the catalogue changes even if old spans remap. Require controls with matching catalogues and inputs to keep claim IDs with zero embedding, Selection or Claimify calls. |
| Split-to-split heading edit | Rename a section heading from `Orchard` to `Terrace` outside the three slots. Hold the document title, slot bytes, relative origins, catalogue and D80 inputs fixed. Keep the D140 date and reuse verified vectors. Miss Selection and Claimify reuse for the affected target and reject `Orchard` as ungrounded added context. With a change only to a preceding D122 producer's location pairs, including a zero-card producer, reuse the consumer's Selection and rerun Claimify. The matching-heading control keeps claim IDs with zero embedding, Selection or Claimify calls. |
| Multiple extraction donors | Give `Orchard` and `Terrace` in an earlier split grid matching Selection and Claimify hashes, with `Orchard` first by ordinal. For an unchanged `Terrace` target, require both stages to reject `Orchard` on typed location, reuse the committed `Terrace` results and claim IDs, and make zero Selection and Claimify calls for the target. |
| Cross-layout citations | Hold the local slots and preceding eight Selection inputs and results fixed while a split child shifts ordinals. Exercise a secondary citation to the next same-owner chunk across the child and an admitted card support outside the old ordinal window. Test both layout directions with different stored stage hashes, including a pre-D162 fitting donor with no lookup digest. Require the same claim IDs, exact remapped spans and zero Selection and Claimify calls for matching targets. |
