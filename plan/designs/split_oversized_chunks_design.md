# Split oversized chunks in E1 (D162)

**Status** Proposed. This is the contract on acceptance. The [v0.22.4
audit](../analysis/split_oversized_chunks_analysis.md) records the incident,
source paths and benchmark measurements.

The packer counts whitespace tokens and can emit a large block intact. D80
then prepares the embedding input, which can exceed the provider limit in
any converter reading, including search-only text. D162 splits only the
offending passages, embeds each complete prepared piece and keeps fitting
legacy grids on their existing identities.

## 1. Size contract and split points

The packing target is **400 whitespace tokens** with anchor modulus 24 and
minimum gap 200. The hard ceiling covers the **full D80 input** including
its location header, separators and model framing or special tokens. A
binding pins tokenizer implementation and data, framing and effective
limits across a closed set of endpoints and their gateways. Qualify both
individual inputs and whole requests, including item count, total framed
tokens and serialized bytes including request overhead. A proven fixed
batch bound can satisfy the request limits. Every admitted input must fit
a singleton request. For OpenRouter, a hard allowlist must cover every
reachable endpoint, since preference order permits marketplace fallback.
[Routing code](../../src/rememberstack/adapters/openrouter.py#L1001-L1034).

Stock self-host Compose with automatic routing uses the legacy ingestion
path. Preference-only routing also uses that path until the operator
configures and activates a qualified closed binding. Setup verifies the
effective model, dimensions, endpoint set and actual routing payload against
the qualified fingerprint before admitting paid ingestion to D162. Show
which binding needs qualification on an activation failure. After activation,
a mismatch blocks new paid work on the affected route until it is qualified
again. CHUNK, embedding dispatch and replay check the pinned envelope.
[Compose defaults](../../compose.yaml#L28-L36).

A cheap upper bound within the limit proves fit. If the bound exceeds the
limit, the binding must decide actual fit with the exact pinned counter or
an equally conclusive offline check. Custom bindings supply the same proof
through the provider port. Qualify that the complete prepared input reaches
the model without truncation and that any minimal nonempty piece fits with
the maximum applicable D80 header and framing. Require evidence of full
delivery at the model boundary, including at the limit. Check fit across
all allowed routes. The qualified envelope fingerprint pins these proofs
and limits for cutting, batching and replay.

For the hosted Qwen/Nebius pin, start qualification at **16,384 Qwen tokens**.
NFC UTF-8 bytes plus the template end token give a fast upper bound for
that tokenizer. Count exactly when the bound cannot prove fit, with any
additional route framing accounted for. **22,528 Qwen tokens** is a separate
trial ceiling for LoCoMo's pinned binding, subject to §5.

1. Run the existing whole-block packer on the committed tree and extraction
   eligibility. Repair starts with the verified predecessor spans (§3).
   Render each candidate with the legacy grid's D80 facts and count. Keep
   the legacy packing verbatim when all inputs fit. An empty reading keeps
   its empty-grid completion.
2. Refine each offending candidate inside its exact source span. Apply D133
   eligibility to the committed derivation ranges, including text between
   blocks. Unlabelled text inherits the parent's eligibility. Every change
   in eligibility is a required cut and bounds the natural units used for
   anchor selection. Within each run of equal eligibility, boundary tiers
   from strongest are block and recorded source seams
   (pages, turns, cells and rows), YAML document or sibling key/item seams
   outside block scalars or prose sentence ends, line ends, whitespace, and
   Unicode scalar boundaries. Code and tables use line ends within blocks.
   Pin the lexical rules. Before packing, form natural units at block and
   source seams and at the strongest structural tier available inside each
   block. Run D58's hash and minimum-gap rule on the exact bytes of these
   nonempty units, starting at the candidate's beginning. Only accepted
   anchors reset spacing, and they cut before their units.
3. Take the next eligibility change, accepted anchor or candidate end,
   whichever comes first, as the hard endpoint. Set the probe goal at that
   endpoint or the end of the target-th whitespace token (**400** by
   default), whichever comes first. Extend each tested cut across following
   whitespace up to the hard endpoint or the next block start. Keep CRLF
   together within those bounds. Keep leading whitespace with the piece
   and emit a whitespace-only run intact with D80's empty-body skip.

   If the piece starts at a block start, allowing whitespace before it,
   try complete-block runs whose content ends by the goal, furthest first.
   Take the first legal run end whose full D80 singleton request fits,
   even before the midpoint of the probes below. Otherwise cap the probe
   goal at the next block edge as well. This gives the next intact block
   its own fit check after a split block or a gap.

   Probe prefixes of 1, 2, 4, … Unicode scalars from the first
   non-whitespace scalar, capped at that goal. Keep the last proved fit
   before overflow or the goal if reached. Take the cut if it reaches the
   hard endpoint or next block edge. Otherwise choose the strongest fitting
   boundary tier in the latter half of the proved reach by scalar distance,
   then the furthest cut in that tier. The proved prefix is the scalar
   fallback. An edit may move cuts up to the next unchanged accepted anchor
   or candidate end.
4. Assess splits under multi-chunk header semantics. For a failing legacy
   singleton without a required interior eligibility cut, cap the first
   cut at the last interior scalar boundary that leaves nonempty content
   on both sides. In a multi-chunk grid, D80 renders fitting candidates
   the same way as before. Re-render the complete grid with its final
   count and prove fit before writing it. Dry-run inventory,
   CHUNK and EMBED_CHUNK use the same renderer and sizing check.
   [Packer](../../src/rememberstack/core/chunker.py#L198-L240),
   [D80 count branch](../../src/rememberstack/core/embedding_input_policy.py#L249-L274).

## 2. Coordinates, evidence and identities

The immutable E0 Markdown, original block grid and selected section
generation are the coordinate authority. Half-open character slices in
Python Unicode code points concatenate to the original parent span. The
pieces may share intersected block ordinals. Associate a piece entirely in
an inter-block gap with the preceding block of its parent. Each piece
records the eligibility of its source slice from §1.
Ownership, page and turn locators and converter origins come from E0.
Clip derivation ranges and evidence catalogs to the consumed slices.

**Occurrence and content.** Only a grid that splits gets a new packing
generation, derived from the legacy parameter stamp, the split and seam
rules and the envelope fingerprint. IDs in that grid derive from the
immutable packing basis and exact span (namespaced UUIDv5). Ordinals express
document order. Whole candidates keep their ordered block-hash identity.
Fragments hash their exact UTF-8 bytes with a fragment tag. A fitting
legacy replay returns its existing IDs. Fresh fitting grids use the legacy
ID allocation.

**Extraction.** E2 takes the closest preceding and following chunks with
the same section owner, even across a child section. Split grids use those
slots for Selection and date-free keys, with clipped origin hashes and
explicit empty slots. Legacy keys use document-adjacent same-owner chunks.
Across either direction of a split/legacy transition, compare the actual
slots from each occurrence's grid for date, Selection and Claimify reuse.
Use the exact predecessor or matching target content in eligible earlier
versions of the lineage to locate candidates when the stored keys differ.
Verify source bytes, relative origins, eligibility, stable header facts
and generations. For extraction also verify the origin date and carry the
frozen typed location elements and derived context E2 consumes. Keep
stored legacy rows and keys intact.
[E1 keys](../../src/rememberstack/workers/e1.py#L734-L771),
[E2 slots](../../src/rememberstack/spine/chunk_catalog.py#L373-L419).

Claimify keeps D122's ordered producers, the target plus its previous eight
document chunks, including empty Selection results. Check equivalent
Selection inputs and the same carried results in every producer slot under
the same reference policy. Use each producer's consumed slots to remap its
cards and supporting spans, together with the target's slots.
Offset translation and exact substring checks must give a unique mapping
(D119). A changed input or failed remap runs ordinary extraction. A split
can affect its same-owner neighbours and up to eight successors of each
changed Selection input. Unaffected targets can reuse their frozen results
and claim IDs even when a legacy slot hash differed.

**Text origin.** Same-version repair copies the exact predecessor parent's
stored D140 date, including unknown. First-ingest replay uses the new-version
date rule and can recover equivalent committed date decisions. A dated new
version takes the earliest eligible origin from a non-deleted earlier version
of its lineage, capped by its own date (D140). Ties use version number, then
ordinal. With no match it uses its own date. An undated version keeps an
unknown origin. For retained whole chunks compare the date-free keys for
the slots E2 consumed.

For a new fragment, also look for its exact byte span in the earlier
version's original parent, even if the parent hash changed. Match stable
undated source facts and generations. Verify the fragment and its actual
same-owner context as ordered slices in the earlier source, with equivalent
eligibility and converter origins relative to each slice. Allow offsets to
move across edits. An ambiguous mapping supplies no date. Use the earlier
parent's committed date, or the matching original fragment's date if that
version was first ingested split. Repair-generated fragments supply no
additional dates. Preserve the recorded origin and the claim's immutable
assertion time. [D140 origin rule](effective_time_and_section_references_design.md#5-reuse-across-versions-text-origin-time).

**Embedding.** Carry a vector only on exact content and full rendered-input
hashes, policy and model matches, the required dimension and proof the
original call delivered the full input. For a fragment covering a complete
contiguous run of blocks with only whitespace padding at its edges,
derive the run's ordered block-hash key from the original grid for vector
lookup. This matches whole and fragment identities in either direction
within the same lineage across non-deleted versions at or before the target
version. Match D80 hashes rendered from each occurrence's own facts,
including its grid's chunk count.

A legacy attestation hash identifies what E1 requested. Use existing
provenance when it ties the call to a historical binding that proves full
delivery for that input, including every route it could have used if the
actual endpoint is unknown. Stamp new attestations
with their qualified binding and delivery evidence. Recover by exact chunk
ID from active or private storage, including an interrupted P1-to-spine
stamp. Verify attestations against the re-rendered hashes. Carry typed
location elements for E2 under its own reuse rules. Re-embed only required
inputs with no qualifying vector.
[E1 recovery](../../src/rememberstack/workers/e1.py#L319-L368),
[attestation write](../../src/rememberstack/workers/e1.py#L535-L572).

Delivery uses the split basis only for affected representations. E0, D80,
eligibility, date-free identity, extractor, reference and normalizer policy
generations keep their pins. Changed inputs incur model work under their
new identities. Retries recover committed products and batch checkpoints.

## 3. Selective migration and replay

### Dispatch and writes

The spine records one pending packing basis and one published passage basis
per representation. A basis pins the representation, original block and
structure generations, frozen rendering inputs, packing generation and
embedding envelope. Claims publication records its own basis (§3 below).
Fitting legacy grids keep their delivery identities. Repair work and
barriers name the basis in their delivery identity and load rows by it.
Reuse of paid products follows §2.

D143 gaps, admissions and frontiers name the packing basis, including when
both layers default to enabled. A replacement gets its own layer decisions.
Retire unentered gaps when that layer's basis is superseded. An E1-only
swap retains obligations on the still-selected claims basis.
Already-admitted facts work on a published claim basis drains to its
frontier before that claim basis is replaced.

If claims are off when a passage repair switches a version with published
claims, record a claims gap for the new passage basis in the same transaction.
This gap is a repair obligation. D143's enable seed and advance admit it
by basis, including for a historical living version. The head-only target
and discharge rules apply to unentered first extraction. Check the
layer, published passage basis, liveness and ordering under the D143
fences. The obligation stays open until claims publication or a superseding
passage cutover transfers it. Archive pauses it and unarchive resumes it.
Version deletion cancels it and forget erases it.
[Layer advance](../../src/rememberstack/spine/processing_layers.py#L275-L354).

Scope the D90 fan-out record, entity units, D118 staging membership and all
admission and completion checks to the exact basis as well as their existing
generation pins. Materialize the replacement's expected claim and entity
sets (or explicit empty completion) from its own completed occurrences.
Admission and final apply require a matching unit and a staging membership
on a basis that carries current testimony. Under the lineage fence, record
membership retirements and wake existing entity work. That transaction
advances an unapplied application's eligibility generation when it loses
its last eligible membership. Bind the admission sequence and prepared
attempt to this generation. Decision publication and final apply compare
it under the application row lock, which also serializes the generation
change with the applied receipt. Retirement needs no entity lock.

For completion, each expected membership has its actual application
receipt or a durable retirement for that basis. Preserve applied receipts
and claim-keyed normalization outputs. Before head selection or re-admission,
the worker checks eligible memberships and discards fenced sequences and
attempts under the canonical entity lock. Repointing can reopen a membership
and unit. A restored unapplied application whose last membership was lost
gets a new sequence and prepares again, even if eligibility returned before
the worker saw the loss. With another eligible membership throughout, the
application keeps its sequence. An applied receipt satisfies a restored
membership.
[D90 fan-out guard](../../src/rememberstack/spine/work_ledger.py#L1538-L1547),
[D118 head and decision](../../src/rememberstack/spine/fact_applications.py#L363-L443),
[completion](../../src/rememberstack/spine/fact_applications.py#L527-L540).

A durable deployment activation fence protects the affected routes. Before
enabling split writes, close database writer admission for incompatible
workers and drain or terminate their sessions. Revoke the old worker
credential and issue writer access only to compatible workers. A deployment
with shared writer credentials rotates that credential at the fence.
Compatible claim and due-route predicates enforce the required worker
generation. Old queued work for unaffected bases stays runnable by compatible
workers. A component stamp in the deduplication key alone cannot enforce
admission. [Claim predicate](../../src/rememberstack/spine/work_ledger.py#L2176-L2192).

When reserving the pending basis, fence replaced jobs from further writes.
Every write, enqueue and barrier completion checks that its basis still
admits that stage, the lineage and version are live, and the leased attempt
is current. Take the D143 layer fence before the lineage fence where both
apply. Provider calls and artifact reads run outside those transactions.
Archive and forget use the same lineage fence.

### Predecessor and replay

Dry-run inventory scans embed dead letters and live ready grids, including
served historical versions, with a bounded, resumable cursor. An existing
D162 publication supplies its predecessor basis directly. For legacy work,
locate the exact ordered chunk IDs using the representation, packing stamp
and durable CHUNK/EMBED_CHUNK evidence for that grid or failed job.
A legacy claim predecessor is selected from completed extraction evidence
independently, including when passages already serve a different basis.
A ready view row or the newest generation alone cannot establish which
grid served. Verify the immutable Markdown digest, recorded spans and
block hashes against the historical `blocks.json`, and the original section
IDs and generation against the recorded structure. Read the sidecar in
its recorded format and verify its coordinates. If decoding requires
reproduction, use the recorded blockizer and verify the resulting grid.

Use saved D80 facts, policy, header and input hash where stamped. For an
unstamped row, recover the exact facts from version-scoped records and
the verified grid, checking any saved P1 attestation. A current lineage
title or current section tree cannot stand in for a historical value.
Read each needed artifact once per representation. Report missing or
conflicting evidence as a repair blocker, with the competing grid IDs
for operator resolution. Require verified evidence for any operator
selection and preserve existing reads until cutover. Audit fitting grids
without enqueueing model work. Revalidate under the fence and record the
verified predecessor row set and artifact digests with the repair basis
when enqueueing. Check the benchmark guard in that transaction.
[Ready view](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L717),
[sidecar loader](../../src/rememberstack/core/blockizer.py#L38-L55).

Replay of an oversized legacy embed dead letter and repair of a proven
oversized ready grid use the same path. Before passage cutover, replay with
the same qualified fingerprint resumes the failed stage on its pending
basis. If an activated qualified envelope differs, reserve a new
pending basis and revoke the previous pending basis's write admission in
one transaction under the fences. Use the verified published grid or the
verified failed legacy grid where available. If that grid now fits, reuse
its cuts and IDs for unfinished delivery. On first ingest without either,
recreate the legacy candidates from pinned E0 artifacts, generations,
eligibility and rendering facts. Verify any committed pending grid with
those pins and the old envelope. Plan from the reproduced candidates
under the new envelope.

Link failed work to its replacement. Use the verified obsolete grid as a
same-version reuse source under §2. Carry equivalent committed vectors,
frozen Selection checkpoints and grounded Claimify results, including
completed empty results and remapped occurrences. Require the same input,
date, producer and grounding checks as on other reuse. These products
stay private until their replacement basis publishes.
[Selection checkpoint](../../src/rememberstack/spine/selection_catalog.py#L140-L201),
[Claimify commit](../../src/rememberstack/spine/claim_catalog.py#L303-L396).

### Publication

After E1 verifies every nonempty piece, admit basis-local E2 under D143.
Keep pending bodies, claims, occurrences and vectors outside the serving
search projections. Grounding opens `embed_claim` on the pending basis.
Reuse attested claim vectors for the same text and binding. A claims
cutover requires completed grounding, a valid vector for each carried
claim, and both replacement claim indexes ready for the post-cutover
population. The predecessor serves both channels until the switch commits.
E3 starts from the published claims basis. Empty extraction follows the
same boundary with an empty claim set.

A sources-only repair switches passages at E1 and keeps the selected claims
basis. Claims enable can then replace that basis, including on a historical
version. When repair publishes both bases together, both projections must
be ready. A new version meets its normal first-serving readiness gate.
[Claim embedding](../../src/rememberstack/workers/p1.py#L99-L144).

**Published claim occurrences.** A version carries a claim when a
`chunk_claims` row belongs to its selected, completed claims basis (reading,
packing and extraction generation). This predicate is independent of the
published passage grid. For P1 nomination and confirmation, D140 and
document scopes, `memory_v1` and evidence hydration, also include exact
occurrences authorized by a D54 verdict (§3 below). Apply scope, currency
and deletion filters to that relation in the same read snapshot. Default
living evidence prefers the standing version when it carries the claim
and otherwise uses the newest eligible published testimony for the claim.
Keep immutable origins on the historical read path. Retained coordinates
and evidence follow D159 erasure.
[Occurrence view](../../src/rememberstack/spine/migrations/versions/p9_31_0052_multi_span_claim_evidence.py#L22-L59),
[scoped hydration](../../src/rememberstack/surfaces/query_engine.py#L6674-L6697).

**Testimony currency.** A snapshot lineage counts carriage on every live
version with published claims. A living lineage counts carriage on its
standing version and on newer versions with completed published claims
awaiting reconciliation. Store the standing version as part of D54
reconciliation. Under the lineage fence, a claims-mode or full reconcile
can advance standing to its own live version once its selected claims
are complete and D143's ordering and earlier facts drain allow it. Do
this even with both layers enabled by default. The version number must
advance, except for repointing on removal. Use the settling version's
source time and sync cycle for the D54 diff, recount and zero-support
policy. Commit them with the standing pointer.

Publish an eligible completed basis with its facts obligation under the
same fences. D143 admits the oldest eligible obligation and lets entered
work drain. A newer source head cannot discharge it. If A stands and C
takes the source head while B finishes claims or runs facts, B still
builds facts when enabled and settles in either order. With facts off, B
can settle via claims-mode reconcile and keep its facts gap while eligible.
A repair of an older version can publish historical occurrences. On
activation, recheck selected legacy bases with completed D143 tails against
the standing and facts rules, including eligible gaps discharged by the
old head-only rule. Use actual currency events and frontier receipts.

Under the lineage fence, diff the predecessor and successor `chunk_claims`.
If repair withdrew a reuse source during extraction, recheck reuse or
grounding only for invalidated attachments. Fresh repair claims are private
and non-current in staging, including in D54 replay. At cutover, set each
fresh claim's initial currency from its eligible published carriage and
record it in the D54 ledger and cache. Record `reextracted` gains for fresh
or reused claims that acquire eligible carriage in the swap.
An A-only claim first found after B settles enters history at cutover
with a non-current `version_superseded` event. A current claim that loses its
last eligible occurrence gets a `reextracted` withdrawal. The D54 review policy
applies if support reaches zero. Commit the search selector, basis,
currency, fact recount, review and evidence-change delta together. Complete
the claims frontier and advance D143 in that commit. Discharge a historical
repair's facts gap while its basis is ineligible and reopen it on eligible
repointing.
[D54 completion](evidence_lifecycle_design.md#5-reconciliation--one-flow-for-both-problems),
[claim default](../../src/rememberstack/spine/migrations/versions/p0_02_0004_claims_facts_evidence.py#L40-L46),
[D143 guard](../../src/rememberstack/spine/lifecycle.py#L158-L197).

**Removal and recovery.** Use selected completed claims bases under the
same lineage fence for version removal, living repointing and D159 archive
recovery. Compare carriage before removal with eligible surviving carriage.
Restore from the repointed version's selected basis. If its claims are still
pending, keep any surviving standing testimony until D143 settles that
version. Unarchive restores eligible published testimony from the archive
episode and runs the same repointing check if the head was removed during
the archive. A changed version coming back from archive restores only
claims it carries when published and eligible.
[Head removal](../../src/rememberstack/workers/reconcile.py#L761-L812),
[unarchive](../../src/rememberstack/workers/reconcile.py#L1049-L1085).

**Reviewed occurrences.** A repair withdrawal review records the exact
previously published occurrence it presents to the reviewer, including its
claim, basis and retained occurrence key. The review hold and verdict check
whether that version is live and eligible to carry testimony under D54.
Under the lineage fence, `restore_support` authorizes the reviewed
occurrence with all its original spans and locators. Commit the
authorization, currency event and recount together.
Read it from the reviewed version and representation. E2 reuse and E3
admission use the selected extraction occurrences.

Re-derivation of the claim on the reviewed version replaces the authorization.
Source supersession and version removal end it. Archive suspends it and
unarchive resumes the same verdict if that exact source still qualifies.
Record these transitions with the currency events so belief-time reads
can place the restoration and the archive interval. Forget erases the
authorization and its evidence. [D54 verdict](evidence_lifecycle_design.md#4-the-counting-rule-d54),
[restore path](../../src/rememberstack/spine/review.py#L352-L402).

**D140 fact windows.** Compute the union of each claim's in-force intervals,
fact period fingerprints and final validation using the selected and
verdict-authorized occurrences joined to `document_version_scope`. If a
cutover or verdict changes which claims an edition carries, enqueue the
existing D140 lineage review for a lineage with declared periods in that
transaction. Key it to the cutover or verdict. Archive and unarchive use
the same occurrence rule and their existing review triggers.
The fan-out finds facts through retained evidence and checks for changed
fingerprints under D140's period fence. If A drops a claim B still carries,
currency and counts can stay the same while the union loses A's interval.
[D140 window review](effective_time_and_section_references_design.md#82-adjudication-input-and-window-review).

#### Search projection publication

Use one serving and one private copy per target. For claims, keep the
search text and attested vector keyed by claim ID in a private P1 projection
separate from the currency cache. Each selected copy has a single HNSW and
BM25 population for the target, with D142's existing score and nomination
rules. The pinned BM25 extension counts physical index entries even during
an open write transaction, so all cutover index writes go to the private
copy.

- For replacements that remove ranking rows, build the private copy
  from a committed snapshot, omitting the batch's retired rows and adding
  its replacements. Copy and build both indexes online. For first
  publication, insert just the new rows into a caught-up private copy.
  Warm this copy in advance and reuse the former serving copy after
  readers drain. Confirm that replay of a prior cutover cleared its retired
  entries from the private indexes before reusing that copy. Ordinary
  fitting-grid passage writes and other routine projection writes continue
  to serve during either build.
- Coordinate the build snapshot and start of change capture under the same
  brief writer fence. Record changed row keys durably with every serving
  write, including updates and removals. Currency changes outside a basis
  switch also update and mark the affected serving claim rows. Reconcile
  marked keys to committed state plus the planned cutover. Clear a key
  only after applying its latest change, preserving concurrent marks.
  Record the switch's changed keys for catch-up of the former serving copy.
- Take the short projection writer fence before the layer and lineage
  fences. Replay only a bounded remainder and revalidate the build
  attempt, active generation, ready index populations and every basis.
  If the remainder exceeds the switch budget, release the fence and
  continue catching up. Switch selectors, readiness and basis pointers
  with their authority changes in one transaction. Switch only projections
  with changed indexed rows. The build lease can renew outside the fence.
  Expiry leaves ordinary writes admitted and allows takeover from durable
  build and replay progress. Rebuild privately if an aborted index write
  or a discarded batch could leave extra index entries. A failed switch
  leaves the serving population intact.

Pin the selected physical relations before planning, in the same snapshot
as the basis and authority, including document and `memory_v1` SQL search.
Hold pins through scoring, confirmation and hydration. Keep old projections
untouched after the switch until their readers drain. If recycling wins the
pin race, restart the snapshot under its deadline. Forget invalidates
affected builds and erases all copies and staging, including reusable
vectors. Ready rows and private vectors otherwise follow §2.
[P1 projection](postgres_p1_search_projection_design.md#7-writes-and-readiness),
[BM25 physical totals](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/scoring/snapshot.c#L178-L204).

#### Passage cutover and reads

Block links choose the first published piece covering the block. Character
links choose the containing piece. For `adjacent_chunks(claim.chunk_id)`,
a chunk ID used by a readable claim as an occurrence or immutable origin
can also resolve when its passage grid was replaced. Use its retained
half-open chunk span to find the intersecting published pieces of that
same visible version and representation under the exact-handle access
rules. Return the covering interval in document order with the usual
window on its two sides. Lookup, expansion and hydration use one snapshot.
The citation keeps its original ID, text and coordinates. A retired ID
without published claim provenance has the ordinary missing-handle result.
[P1 nomination](../../src/rememberstack/adapters/postgres_p1.py#L891-L930),
[adjacency contract](adjacent_chunks_retrieval_design.md),
[citation guide](../../website/src/app/docs/guides/cite-sources/page.mdx#positions-and-surrounding-text).

## 4. Provider rejection and retry

Use the strict decoder and retry path only on the qualified E1 chunk
embedding call. E1 checks every prepared input and outgoing batch before
calling the provider. A proven singleton excess fails with `embedding.input_too_large`
and points to chunk repair. Fill batches in grid order, flushing before the
next item would exceed any qualified request limit (§1). Preserve structured
input and length errors before capacity retries. Bisect a deterministically
rejected batch to isolate single-input failures and checkpoint valid siblings.
OpenRouter and other indexed protocols must return each zero-based request
position exactly once, with a non-Boolean integer index on every item.
Check the count and mapping, then run `require_p1_vector` on every mapped
item. This checks dimension, finiteness in stored float4 and nonzero norm.
Any failure rejects the whole batch as a typed invalid response with its
provider usage. Only independently validated subrequests can checkpoint
successful siblings. [P1 validator](../../src/rememberstack/ports/p1_index.py#L26-L49).
A confirmed deterministic singleton rejection fails the job with
`embedding.input_too_large` or `embedding.input_rejected`, according to the
evidence. These failures are non-retryable. Report chunk and representation
IDs, count or bound, model, endpoint and provider status.

Generic 429s, timeouts and upstream outages use the existing bounded
capacity retries. An unusable singleton response fails the affected work.
Every nonempty input admitted under D162 requires a valid vector, including
fitting legacy-layout grids. Only a D80 empty body can complete with a
skip. Replay after a qualified envelope change uses the basis selection
in §3. Public query, claim, profile and fact embedding use the ordinary
provider call path. [Current E1 rejection path](../../src/rememberstack/workers/e1.py#L393-L513).

## 5. Retrieval, release gates and rollout

### Benchmark admission

Protect both the LoCoMo and BEAM deployments with a durable admission guard
checked by ingest, inventory, replay and reader selection. Keep protected
stores read-only on their pinned reader and compatible schema until the
exact candidate reader and schema pass parity on copies. Block split
admission and repair of the protected corpus. Bind qualification to the
frozen manifest, grid, ingestion binding, reader and schema revisions, and
the effective query embedding path. Pin query preparation, adapter and
retry behavior, model and dimensions, gateway and actual routing payload
including reachable endpoints and fallback. A drifted query binding cannot
use an earlier reader qualification.

- **Frozen input and grid.** Verify the exact prepared readings, including
  LoCoMo's frozen OCR and BEAM's retained rendering. Dry-run the candidate
  packer under the qualified envelope. Ordered spans, bodies, full input
  hashes and eligibility must match the legacy grid. The retained stores
  must keep their published grid digests and IDs, with zero
  ingest-model calls and zero repair writes during qualification.
- **Tool response parity.** Replay the recorded requests on the pinned and
  candidate public tool paths with the same filters, evaluation times and
  planner settings. Fixed-vector replay isolates ranking once the query
  path is proven identical. For any query embedding change, replay captured
  raw provider responses and errors at the transport boundary through both
  public paths, checking the outgoing payloads, retries and statuses.
  Include a singleton at index `1`, which the shared decoder accepts.
  Qualify route changes with responses captured on the corresponding
  pinned and candidate routes. Compare the complete answerer-visible
  responses and their ordering, including IDs, evidence, scores, statuses, pagination,
  BEAM SQL columns and values, and LoCoMo's public read tools, including P3
  reads in the trace. Apply only the harness's existing presentation rules.
  Any difference blocks the reader change. The scorer smoke is a separate check.
  [Query embedding call](../../src/rememberstack/surfaces/query_engine.py#L4838-L4866),
  [OpenRouter route and decoder](../../src/rememberstack/adapters/openrouter.py#L1001-L1077),
  [BEAM SQL reads](../../benchmarks/rs_harness_beam/answer_agent.py#L511-L525),
  [LoCoMo public dispatch](../../benchmarks/locomo/retrieval.py#L165-L202).
- **Provider fit.** The archived LoCoMo audit reports a 21,567-token
  singleton before its header and a 22,337-token upper bound across the
  v45 candidates. A 16,384-token cap changes that grid. Qualify the exact
  protocol binding (`nebius`, order unset) for the actual frozen inputs.
  The 22,528 ceiling is a trial, and a lower one requires exact fit
  verification on the frozen readings. The runner rejects binding changes.
  An owner-approved binding or retrieval comparison uses a separate run
  identity and keeps the published 94.09% result and its attestation.
  [Fixture audit](../analysis/split_oversized_chunks_analysis.md#benchmark-fixture-audit),
  [binding](../../benchmarks/locomo/protocol.py#L127-L134),
  [runner guard](../../benchmarks/locomo/runner.py#L1714-L1729).

### Release checks

| Gate | Pass condition |
| --- | --- |
| Packing | Fitting output is byte-identical across converters. Test header-only overflow, YAML, OCR tables, long turns, CRLF and scalar fallback. Give inventory, fresh ingestion and repair the same oversized candidate with equal-tier cuts at 400 and 600 tokens that both fit and an anchor at 520. All three cut at 400 first, then respect the anchor. In an oversized candidate with eligible prose on both sides of a parser-skipped HTML block labelled `code`, preserve every byte in order, cut at both eligibility changes and give each piece a single eligibility. Verify clipped origins and that E2 skips the ineligible pieces. |
| Binding and provider conformance | Verify stock Compose and order-only routing use legacy admission. Verify a closed binding activates and effective route drift blocks paid D162 admission. Prove full delivery at and below the ceiling, including exact fallback for an inconclusive bound and a framed minimal piece. Verify splitting above the ceiling. Give E1 fitting items that together exceed the request limit. It must send only fitting batches. |
| Replay | Resume at the same fingerprint, then replay at a different qualified envelope after partial durable work. Cover both repair and a first ingest with vectors, Selection and grounded Claimify checkpoints before first publication. Change a cut and assert zero paid calls for every equivalent committed input. Reject obsolete writes and keep any prior publication serving until cutover. |
| Extraction and dates | Across two dated versions, test legacy → split and split → fitting with an untouched parent spanning a child and the oversize elsewhere. Assert the same origin dates and claim IDs and zero paid calls for unchanged inputs. Edit one line inside a previously unsplit oversized parent and test an untouched fragment beyond an unchanged anchor with matching context. Run the earlier repair before and after ingest of the edit. Check inherited origins and relative-date claims in both orders. |
| Predecessor and vectors | Test two legacy generations and an older sidecar. Ambiguity preserves existing reads. Recover an unstamped P1 vector by predecessor ID with full-delivery provenance and reuse it after cutover. Replace a matching-hash vector whose historical route truncated or cannot prove delivery. Append a dense paragraph with a fitting block seam before the probe midpoint and no anchor at that seam. Cut at the seam and assert zero embedding calls for the preserved block with identical D80 input and delivery proof. |
| Claims publication and adjacency | Stall claim embedding after replacement grounding. The predecessor still answers semantic and BM25 claim searches. Resume and verify both channels return replacement claims immediately on commit. Repair A before, during and after B's publication and reconcile. Check reattachment, last-occurrence loss, recount and review. Call `adjacent_chunks(claim.chunk_id)` after E1-only cutover and again from a scoped read after re-enable but before claims cutover. Check the covering pieces, flanks, citation coordinates and history. |
| Ordered testimony and facts | Start with A standing, B entered in claims and both layers enabled. Let C take the source head before B's facts admission in one run and after it in the other. Hold C pending for claims. B must build its B-only fact and settle in both runs. Compare standing, fact support, A-only withdrawal and paid products. Repeat with facts disabled as B settles, then enable facts with C still pending and verify B's obligation is admitted. |
| Historical repair and recovery | With B standing, repair A while claims are off. Re-enable claims with A still historical. Verify its repair obligation publishes the new claims basis and an A-only claim is readable in history without current fact admission or support. Delete B with both a retired-only and a pending-only attachment on A. Only A's selected complete basis restores testimony. Repeat with B removed during an archive and verify unarchive. |
| Fact application retirement | Retire one of two eligible memberships and verify the unapplied application keeps its sequence. Retire the last during a prepared attempt. Restore eligibility by repointing before the entity worker sees retirement in one run and after it clears the sequence in another. In both runs reject the late response, re-enter with a new sequence and apply once. Check that other stream work can advance, retirement satisfies the old basis and an applied receipt satisfies a restored membership. |
| Fact windows | In a snapshot lineage, the same claim supports a fact through editions A and B with distinct in-force intervals. Repair A in the same representation so it drops the claim. Assert unchanged currency and support count, loss of A's interval, a changed fingerprint and a D140 review. A decision prepared with the old union must fail validation or leave a stale basis for the queued review. |
| Reviewed restoration | Repair a dated snapshot edition so its sole supporting claim is dropped, then restore the exact reviewed occurrence by verdict. Verify one supporting lineage, the correct quote, spans and locators in claim and fact reads, and the edition's D140 interval and refreshed period basis. Archive and unarchive it. During archive the grant supplies neither live evidence nor periods. Afterwards the same occurrence and support return. Check a removed version cannot be restored by verdict. |
| Fences and ranking | Check interrupted E1/E2, obsolete attempts, layer switches and archive/forget. For passage and claim semantic and BM25 searches, compare IDs, order and scores before staging, during a held-open switch and after rollback under D142 planner settings. Check reference links and adjacency. On commit, old readers keep the pinned population and new readers see the replacement. Run D142 recall, plan and latency gates. [D142 checks](../../benchmarks/p1_ranked/README.md) |
| Online projection | At corpus scale, sustain inserts, updates and removals through an online build longer than the switch budget. Interrupt and resume catch-up. A repair must publish with no lost writes and writer stalls bounded by the final switch budget. First publication on a warmed copy must publish under the same load without a corpus copy or full index rebuild. Report build time, peak space and writer stall time. |
| Error handling | Genuine 429s retry and deterministic rejection is typed. Cover valid shuffled indices, missing and duplicate indices, and an otherwise valid indexed batch with one good vector and one zero-norm vector. A malformed batch attests nothing and a separately validated sibling can checkpoint. A malformed singleton blocks both layouts. Check the incident's actual rendered sizes. |

Apply additive schema and compatible readers per D160. Warm the search
copies and activate workers through §3's fence. Run dry inventory, then
canary the two affected hosted documents. Check source coverage, ordered
retrieval and paid-call deltas before bounded expansion. Rollback pauses
admission, keeps the worker fence active and serves published bases with a
compatible reader.
