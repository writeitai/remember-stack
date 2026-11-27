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
and effective `text_origin_at`. On a new version, choose among matching rows
in earlier non-deleted versions using D140's date eligibility and ordering.
An undated version can keep matching keys with an unknown date. On a
same-version repack, compare with the preceding completed grid (§3).

If the split-aware comparison has no match, hash the final stable inputs.
Resolve the date under D140 for a new grid and use the effective date in §3
for recovery. Include it in the Selection key. Tag both new keys `d162:`
so subsequent grids recognize them even after the neighbor fits.

Selection and Claimify try an intact donor from the pinned preceding
completed grid before the ordinary earlier-version lookup. Verify the
consumed inputs above even for matching legacy keys. Require a committed
Selection result at the matching Selection key and extractor generation.
For Claimify also require a committed completion at the full D122 key,
including the ordered producer keys and reference policy. Verify the donor's
inputs on the split-aware earlier-version path too. Load each window from
its recorded grid and remap cards and every claim span under D119.
Successful reuse attaches the original claim IDs to the new occurrence.
A missing match or ambiguous remap falls back to the usual lookup or fresh
work. Retries within a grid replay the target row's own results.
[E1 reuse](e1_chunks_design.md#7-reuse-mechanics-d56-bound-here-amendments-a1a3) ·
[D140 text origin](effective_time_and_section_references_design.md#5-reuse-across-versions-text-origin-time).

## 3. Recover through CHUNK while the grid is unpublished

### Delivery and completion

A grid is the pair `(representation_id, full chunker_version)` for a
document version. Under the lineage lock, record the version's selected
CHUNK row when admitting a new grid and assign version-level ledger keys
under that same lock. Bind existing keys to their grids where retained
evidence proves the pair. Use the base component version at an unused key
and append an unambiguous encoding of the pair for additional grids.
Repeated enqueues resolve to the assigned key. Every version-level job
from CHUNK through the applicable tail records the pair at enqueue and
uses it at dispatch. At same-version repack admission, pin the most
recently admitted earlier grid of that version with complete occurrences
as the reuse source.
[D143 occurrence completion](processing_layers_design.md#51-gaps-and-frontiers).

For a legacy sources-only completion with deferred gaps and no chunk or
flush rows, bind the original successful work using the proven historical
full generation. If the generation is unknown, verify from the immutable
E0 artifacts that the representation named by the successful embed row
has no blocks to pack. Under the lineage lock, recheck the representation
and bind the existing completion once to an empty grid at the configured
full generation. Keep the original keys and `finished_at`. Deferred D143
admission uses that binding and takes the ordinary empty path.

E2 starts after that grid's embed row succeeds, including the empty case.
Immediate and deferred D143 admission use the recorded grid for Selection and
Claimify dispatch, windows and barriers. For older chunk-grain E2 work, the
target chunk identifies the pair. Derive the tail's distinct claim IDs from
`chunk_claims` in that grid at the pinned extractor generation.
`embed_claim` fills missing vectors for that set. D56 governs input reuse.
Reuse claim vectors and normalization outputs by claim ID and generation.

- **Facts entered.** Pin the composed D90 flush generation at facts
  admission. Use it with the grid to materialize the flush marker and units,
  match expected application receipts, and close the barrier or record
  empty completion. Release `adjudicate_supersession` and `embed_claim`
  together. Supersession chains full `reconcile`, which opens `label_relation`.
  A distinct admitted flush generation on the same grid gets distinct
  delivery keys for supersession, full reconcile and labeling under the
  same key assignment rule.
  [Observation flush](e3_entity_obs_flush_fanout_design.md#51-stored-version-and-entity-membership).
- **Claims-only tail.** When D143 opens this tail, dispatch `embed_claim`
  and claims-mode `reconcile` for the grid. The base reconcile component
  version identifies the mode even when the ledger key includes the grid.
  [Processing layers](processing_layers_design.md#42-the-claims-tail-and-the-two-reconcile-modes).

Readiness and receipts check the expected grid's work and, for facts,
the flush generation recorded at admission. D143 admits and closes gaps
for the selected grid under the lineage lock. Both reconcile modes check
selection under that lock and retire testimony lost from any displaced grid
of that version according to D54. Verified reuse on the completed selected
grid can restore packing-only losses. Late claims from a displaced grid are
historical. Living supersession checks the current version's selected occurrences.
[Testimony currency](evidence_lifecycle_design.md#3-testimony-currency-d54).

### Fact-application fence

Before changing selection under the lineage lock, check the old grid's
memberships across flush generations for D118 assertions with an
admission sequence and no applied receipt. If any remain, keep that grid
selected and retry the repack through the ledger. Release the lock so the
entity stream can drain.
A dead-lettered admission blocks the switch until replay applies it or
hard forget removes it.

Recheck selection under the same lock at D143 facts entry and when D118
assigns an admission sequence. Each new assertion admission requires a
materialized membership on a selected grid at a flush generation admitted
to facts. Acquire the identity fence, candidate lineage locks in ID order,
then the entity stream lock, matching the support-review lock order. Recheck
membership and selection before committing admissions for those lineages.
Already-admitted assertions finish in D118 order.
[D118 application](mutable_fact_windows_design.md#4-writes-concurrency-and-retry) ·
[D143 ordering](processing_layers_design.md#43-the-claims-tail-waits-for-earlier-fact-work-configured-deployments).

### Private size recovery

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

Size recovery names the failed `embed_chunk` row and attempt number. Load
the grid's immutable E0 inputs and take the lineage and work-row locks. If
the row advanced past that dead letter, return its current state and
re-announce any pending work. A later failure requires its own attempt
number. Verify the exact failed attempt, liveness, E1 incompleteness and
continuous isolation under the locks.

Invoke CHUNK's shared fit-and-refine routine directly in that transaction
with the qualified binding for the resumed embed. Ordinary CHUNK delivery
replays committed cuts. Use those cuts as bounds so each new piece has one
recorded parent. Take `text_origin_at` from each intact row or piece's parent,
including unknown.
For an unstamped pre-D140 intact row with both `text_origin_at` and
`reuse_identity_hash` unset, use E2's legacy fallback from the same version
(`source_modified_at`, then `published_at`, then unknown). Store the effective
date on rebuilt rows and use it in the date-sensitive Selection key under
§2, the E2 header and fresh claims' `asserted_at`.

Replace any recut private rows, remove their superseded embedding stamps and
P1 rows, and reopen only the failed embed row with its attempt count and error
history. If all cuts fit, commit just the replay transition. Announce pending
work after commit. On replay, stamp D80 metadata from the final grid on all
surviving rows that need a refresh, including when only `chunk_count` changes
in `location_facts_json`. Keep a vector that still matches the rendered input,
policy and model. E1 verifies final-grid stamps before completion, and its
writes verify liveness and a running, unexpired attempt under the same locks
through commit.

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
completes. Run with facts enabled and disabled. Require a new claim to get a
stored vector and the new grid to finish its applicable tail, including
flush, full reconcile and labels with facts enabled, or claims-mode
reconcile with facts disabled. The facts-enabled case reuses a subject from
the old flush and requires a new unit and applied receipt for the new claim.
Verify that reconcile retires old-only testimony, that prior ledger keys resolve,
and that readiness and receipts wait for the new grid. Cover immediate and
deferred claims handoffs and empty completion.

On the first version of a lineage, change the budget so only the Appendix
repacks. An intact chunk with matching D80, Selection and Claimify inputs
gets a new chunk ID, the same claim IDs with remapped evidence, and zero
paid calls for all three stages. Change a keyed producer or make a span
ambiguous and require fresh work for the affected stage.

In snapshot mode, complete grid A, admit B and displace it before it
reconciles, then complete C. Exercise both reconcile modes. A-only current
testimony retires as `reextracted` and support is recounted. Claims carried
by C or another still-current snapshot basis retain currency.

Pause an old grid's fact application with a durable decision waiting to
apply. Request a repack and verify the old grid stays selected. Resume
and require one application receipt before selection switches. After
the switch, let a helper for the same subject see a staged, unadmitted
assertion unique to the old grid. Its admission sequence stays unset
and the new grid's recount reflects all committed old applications.

After a packing-only repack retires a claim, ingest a dated successor
whose intact chunk matches the old Selection and Claimify keys and
remaps the old spans. Exercise both reconcile modes. Require the same
claim ID in current-only search, one restoration event across retries,
and recounted support with any matching withdrawal flag resolved. Check
that the carried chunk reuses its paid results and that an occurrence on
a displaced grid leaves the claim historical.

Upgrade a legacy empty sources-only completion with deferred claims and
facts gaps, a succeeded embed row, and no chunks or flush marker. Cover
both proven historical parameters and verified empty E0 with unknown
parameters. Enable claims with a different packing configuration.
Require the same binding and original sources completion time. Deferred
admission takes the empty path with zero chunk jobs. A nonempty
representation with missing chunks fails the empty-grid check.

With CHUNK already succeeded, dead-letter private `embed_chunk` on size and
issue repeated recovery requests for that exact failed attempt while a newer
grid exists. Require one recut and one replay allowance, the same succeeded
CHUNK row, and the same embed row and preserved error history. E2 must use
the recovered grid even when its configured generation differs. A repeated
request for the first failure must leave a later dead letter alone. An
ambiguous legacy failure stays dead-lettered. Increase the chunk count
by recutting a different chunk while a stamped survivor's D80 input stays
identical. Check the final count in its stored location facts, the same
vector and zero additional provider calls for that survivor.

In private recovery, recut a dated grid's neighbor while the intact target's
recorded origin predates the version. Check that the rebuilt Selection key,
E2 header and fresh `asserted_at` use that origin. Repeat with unstamped
pre-D140 rows using a source modification date, a publication date alone
and no date. Verify that an otherwise identical undated occurrence has a
different Selection key from a recovered dated occurrence.

With multiple grids in one representation, both section-link reads retain
legacy IDs and pick only the first intersecting chunk of the split grid.
