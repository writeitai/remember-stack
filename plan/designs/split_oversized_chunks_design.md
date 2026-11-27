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
2. Refine each offending candidate inside its exact source span. Boundary
   tiers, from strongest, are block and recorded source seams
   (pages, turns, cells and rows), YAML document or sibling key/item seams
   outside block scalars or prose sentence ends, line ends, whitespace, and
   Unicode scalar boundaries. Code and tables use line ends within blocks.
   Pin the lexical rules. Before packing, form natural units at block and
   source seams and at the strongest structural tier available inside each
   block. Run D58's hash and minimum-gap rule on the exact bytes of these
   nonempty units, starting at the candidate's beginning. Accepted anchors
   cut before their units. Size cuts do not reset anchor spacing.
3. Set the goal at the next accepted anchor, candidate end or end of the
   target-th whitespace token (**400** by default), whichever comes first.
   Probe prefixes of 1, 2, 4, … Unicode scalars from the first non-whitespace
   scalar, capped at the goal. Extend each cut across following whitespace
   up to the anchor or candidate end before testing fit. This puts both
   characters of CRLF on the same side. Keep the last proved fit before
   the first overflow, or the goal if reached. If that reaches the anchor
   or candidate end, take the cut. Otherwise take the strongest fitting
   boundary tier in the latter half of the proved reach by scalar distance,
   then the furthest cut in that tier. The proved
   prefix itself is a scalar fallback. Test each selected cut against the
   full D80 singleton request. Leading whitespace stays with the first
   piece. An edit may move cuts up to the next unchanged accepted anchor
   or candidate end.
4. Assess splits under multi-chunk header semantics. For a failing legacy
   singleton cap the first cut at the last interior scalar boundary that
   leaves nonempty content on both sides. In a multi-chunk grid, D80 renders
   fitting candidates the same way as before. Re-render the complete grid
   with its final count and prove fit before writing it. Dry-run inventory,
   CHUNK and EMBED_CHUNK use the same renderer and sizing check.
   [Packer](../../src/rememberstack/core/chunker.py#L198-L240),
   [D80 count branch](../../src/rememberstack/core/embedding_input_policy.py#L249-L274).

## 2. Coordinates, evidence and identities

The immutable E0 Markdown, original block grid and selected section
generation are the coordinate authority. Half-open character slices in
Python Unicode code points concatenate to the original parent span. The
pieces may share intersected block ordinals. Associate a piece entirely in
an inter-block gap with the preceding block of its parent. Apply parent
eligibility in unlabeled gaps and D133 at labeled eligibility transitions.
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
stored D140 date, including unknown. A dated new version takes the earliest
eligible origin from a non-deleted earlier version of its lineage, capped by
its own date (D140). Ties use version number, then ordinal. With no match
it uses its own date. An undated version keeps an unknown origin. For
retained whole chunks compare the date-free keys for the slots E2 consumed.

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
original call delivered the full input. A legacy hash identifies what E1
requested. Use existing provenance when it ties the call to a historical
binding that proves full delivery for that input, including every route it
could have used if the actual endpoint is unknown. Stamp new attestations
with their qualified binding and delivery evidence. Recover by exact chunk
ID from active or private storage, including an interrupted P1-to-spine
stamp. Re-render with frozen predecessor and final successor D80 facts to
verify both hashes and preserve typed location elements for E2. Re-embed
only required inputs with no qualifying vector.
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

D143 gaps, admissions and frontiers name the packing basis. A replacement
gets its own layer decisions. Retire unentered predecessor gaps when their
basis is superseded. Already-admitted facts work on a published claim basis
drains to its frontier before that claim basis is replaced. The D143 rules
for missing layers in living and snapshot lineages still apply.

Scope the D90 fan-out record, entity units, D118 staging membership and all
admission and completion checks to the exact basis as well as their existing
generation pins. Materialize the replacement's expected claim and entity
sets (or explicit empty completion) from its own completed occurrences.
An application can enter the existing entity stream only through matching
staging and a materialized unit on the same eligible basis. Revalidate
that membership at apply. Complete a basis using its own units and their
application receipts. Claim-keyed normalization outputs and receipts can
satisfy new memberships. Keep the entity stream's ordering and semantic
generations.
[D90 fan-out guard](../../src/rememberstack/spine/work_ledger.py#L1538-L1547),
[D118 admission and receipt retirement](../../src/rememberstack/spine/fact_applications.py#L363-L399).

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
one transaction under the fences. Plan from the verified published grid,
or the failed legacy grid if the reading has yet to publish. If that grid
now fits, keep its cuts and IDs and retry unfinished delivery. Retain the
serving pointers and indexes until a verified cutover. Link failed work to
its replacement. Recover any qualifying attestations from obsolete staging
under §2.

### Publication

Keep pending bodies and vectors outside both live `chunk_search` indexes.
After every nonempty piece has a verified embedding, open basis-local E2
under D143 when required. Keep successor claims and occurrences private to
extraction and out of claim search until publication. The predecessor serves
through the completed Selection and Claimify/grounding barrier. Sources-only
repair switches passages at E1 and leaves the published claims basis selected.
A completed empty extraction can publish an empty basis.
A new version meets its normal first-serving readiness gate. A failed
cutover leaves serving pointers in place and staging recoverable.

**Testimony currency.** For a living lineage, use its last completed
published testimony basis under the lineage fence. Apply the D143 guard
even when both layers are enabled by default. Source reconciliation
advances the standing version only against complete published occurrences.
E0 can advance the source head while its claims are pending or disabled.
While B is in that state, a repair of standing version A tests carriage on
A's replacement. Once B settles, a repair of A changes its historical
evidence. Carriage on a newer
completed published basis also protects a claim while that version awaits
its guarded supersession. For snapshot lineages count carriage on all live
published versions.

Under the same fence, diff the predecessor and successor `chunk_claims`
attachments, which record where each basis carries the claim.
Revalidate staged reuse against published carriage before completing the
barrier if a repair withdrew its source claim during extraction. Re-run
reuse checks or grounding only for invalidated attachments. A repair
withdraws current testimony only when the swap removes its last eligible
published occurrence, regardless of its origin. Publish the new occurrences,
append D54 `reextracted` events, update the currency cache, recount facts and
record any zero-support review and evidence-change delta in one transaction.
Complete the barrier and open the applicable D143 tail in that commit.
Ordinary source supersession uses published occurrences and retains its
D54/D143 ordering and closure rules.
[D54 completion](evidence_lifecycle_design.md#5-reconciliation--one-flow-for-both-problems),
[source head update](../../src/rememberstack/spine/document_catalog.py#L827-L852),
[D143 guard](../../src/rememberstack/spine/lifecycle.py#L158-L197).

**Claim evidence reads.** An occurrence is published when its `chunk_claims`
row belongs to the live version's selected, completed claims basis (reading,
packing and extraction generation). This predicate is independent of the
published passage grid. Use it in P1 claim nomination and confirmation,
D140 and document scope checks, `memory_v1` occurrence views and claim or
fact evidence hydration in the same read snapshot. Default living evidence
uses the standing testimony version. Apply existing scope, currency and
deletion filters. Keep immutable origins on the historical read path.
Retained coordinates and evidence follow D159 erasure.
[Occurrence view](../../src/rememberstack/spine/migrations/versions/p9_31_0052_multi_span_claim_evidence.py#L22-L59),
[scoped hydration](../../src/rememberstack/surfaces/query_engine.py#L6674-L6697).

**Passage reads.** Switch the published passage pointer and its active
`chunk_search` rows atomically, moving retained vectors to private storage.
When E2 is required, this is the claims cutover transaction. Representations
awaiting verified cutover retain their legacy reads. P1 nomination and
confirmation, `memory_v1` chunk views, document search, reference links and
adjacency use the published passage basis through hydration in one read
snapshot. Block links choose the first published piece covering the block
by source order. Character links choose the containing piece. A superseded
ID is invisible to live adjacency.
[P1 nomination](../../src/rememberstack/adapters/postgres_p1.py#L891-L930),
[adjacency](../../src/rememberstack/surfaces/query_engine.py#L2882-L2917).

## 4. Provider rejection and retry

E1 checks every prepared input and outgoing batch before calling the
provider. A proven singleton excess fails with `embedding.input_too_large`
and points to chunk repair. Fill batches in grid order, flushing before the
next item would exceed any qualified request limit (§1). Preserve structured
input and length errors before capacity retries. Bisect a deterministically
rejected batch to isolate single-input failures and checkpoint valid siblings.
A confirmed deterministic singleton rejection fails the job with
`embedding.input_too_large` or `embedding.input_rejected`, according to the
evidence. These failures are non-retryable. Report chunk and representation
IDs, count or bound, model, endpoint and provider status.

Generic 429s, timeouts and upstream outages use the existing bounded
capacity retries. An unusable singleton response fails the affected work.
Every nonempty input admitted under D162 requires a valid vector, including
fitting legacy-layout grids. Only a D80 empty body can complete with a
skip. Replay after a qualified envelope change uses the basis selection
in §3. Query, claim, profile and fact embeddings use their existing
policies. [Current E1 rejection path](../../src/rememberstack/workers/e1.py#L393-L513).

## 5. Retrieval, release gates and rollout

### Benchmark admission

Protect both the LoCoMo and BEAM deployments with a durable admission guard
checked by ingest, inventory, replay and reader selection. Keep protected
stores read-only on their pinned reader and compatible schema until the
exact candidate reader and schema pass parity on copies. Block split
admission and repair of the protected corpus. Bind qualification to the
frozen manifest, grid, exact provider binding, reader and schema revisions.

- **Frozen input and grid.** Verify the exact prepared readings, including
  LoCoMo's frozen OCR and BEAM's retained rendering. Dry-run the candidate
  packer under the qualified envelope. Ordered spans, bodies, full input
  hashes and eligibility must match the legacy grid. The retained stores
  must keep their published grid digests and IDs, with zero
  ingest-model calls and zero repair writes during qualification.
- **Tool response parity.** Replay the recorded requests on the pinned and
  candidate paths with the same filters, query vectors, evaluation times and
  planner settings. Compare the complete answerer-visible responses and
  their ordering, including IDs, evidence, scores, statuses, pagination,
  BEAM SQL columns and values, and LoCoMo's public read tools, including P3
  reads in the trace. Apply only the harness's existing presentation rules.
  Any difference blocks the reader change. The scorer smoke is a separate check.
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
| Packing | Fitting output is byte-identical across converters. Test header-only overflow, YAML, OCR tables, long turns, CRLF and scalar fallback. Give inventory, fresh ingestion and repair the same oversized candidate with equal-tier cuts at 400 and 600 tokens that both fit and an anchor at 520. All three cut at 400 first, then respect the anchor. Verify exact source recovery, eligibility and origins. |
| Provider conformance and replay | Prove full delivery at and below the ceiling, including exact fallback for an inconclusive bound and a framed minimal piece. Verify splitting above the ceiling. Give E1 items that fit individually and exceed the request ceiling together. It must send only fitting batches. Repeat a failed repair at the same fingerprint, then replay with a qualified second envelope while staging is durable. Reject obsolete writes, preserve the published grid and reuse qualifying staged vectors. |
| Extraction and dates | Across two dated versions, test legacy → split and split → fitting with an untouched parent spanning a child and the oversize elsewhere. Assert the same origin dates and claim IDs and zero paid calls for unchanged inputs. Edit one line inside a previously unsplit oversized parent and test an untouched fragment beyond an unchanged anchor with matching context. Run the earlier repair before and after ingest of the edit. Check inherited origins and relative-date claims in both orders. |
| Predecessor and vectors | Test two legacy generations and an older sidecar. Ambiguity preserves existing reads. Recover an unstamped P1 vector by predecessor ID with full-delivery provenance and reuse it after cutover. In the same repair, replace a matching-hash vector whose historical route truncated or cannot prove delivery. Check that only that input is called again. |
| Claims and facts publication | Repair A while B serves and is still extracting, after B publishes occurrences, and after its guarded reconcile. Include B initially sources-only. Check reattachment, last-occurrence loss, currency, recount and review. After an E1-only cutover, views and scoped reads still return the published claim evidence. After claims cutover, live reads return the successor's occurrences and history retains the origin. Repair a basis with a completed facts tail and a new application for the same entity. Only the replacement unit admits and completes it. Reused claims keep their receipts and new work runs once. |
| Fences and ranking | Check interrupted E1/E2, obsolete workers and attempts, archive/forget and layer switches. During staging, ordered HNSW/BM25 results and scores, reference links and adjacency remain identical. Run D142's indexed-vs-exact recall, plan and latency gates on a mixed corpus. [D142 checks](../../benchmarks/p1_ranked/README.md) |
| Error handling | Genuine 429s retry, deterministic rejection is typed and successful siblings are retained. Malformed singleton output blocks both layouts. Check the incident's actual rendered sizes. |

Apply additive schema and compatible readers per D160. Activate workers
through §3's fence, run dry inventory, then canary the two affected hosted
documents. Check source coverage, ordered retrieval and paid-call deltas
before bounded expansion. Rollback pauses admission, keeps the worker fence
active and serves published bases with a compatible reader.
