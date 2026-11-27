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
its location header, separators and model framing or special tokens. Each
binding pins its tokenizer implementation and data, framing and effective
limit for a closed set of qualified endpoints. Use the smallest qualified
limit in that set. For OpenRouter, enforce a hard allowlist covering every
endpoint the selector can reach. Provider preference order alone permits
marketplace fallback. [Routing code](../../src/rememberstack/adapters/openrouter.py#L1001-L1034).

A cheap upper bound within the limit proves fit. If the bound exceeds the
limit, the binding must decide actual fit with the exact pinned counter or
an equally conclusive offline check. Custom bindings supply the same proof
through the provider port. Qualify that the complete prepared input reaches
the model without truncation and that any minimal nonempty piece fits with
the maximum applicable D80 header and framing. Require evidence of full
delivery at the model boundary, including at the limit. Admission requires
this qualification. The qualified envelope and its fingerprint are
configuration used for deterministic cuts.

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
2. Refine each offending candidate inside its exact source bounds. Preserve
   every existing cut, including owner, eligibility and anchor boundaries.
   Prefer block boundaries and recorded source units such as pages, turns,
   cells and table rows. Inside a block, prefer YAML document and sibling
   key/item boundaries outside block scalars, then line ends. Use line ends
   for code and tables, and sentence ends then line ends for prose. Pin the
   lexical rules.
3. Find a fitting prefix using the bound on body, header and framing and the
   conclusive fallback where needed. Aim for **400 whitespace tokens** where
   there is a fitting cut. Take the furthest boundary of the strongest tier
   in the latter half of the prefix. Fall through to whitespace, then a
   Unicode scalar boundary. Keep CRLF together and attach whitespace-only
   runs to a neighbouring piece. Preserve every source character. Use the
   existing hash/modulus and minimum gap on natural units within the
   candidate to restart packing at internal anchors. An edit can move cuts
   up to the next unchanged accepted anchor or the candidate end. Reuse
   byte counts and work on bounded prefixes.
4. A failing legacy singleton yields at least two pieces assessed under
   multi-chunk header semantics. In a multi-chunk grid, D80 renders fitting
   candidates the same way as before. Re-render the complete grid with its
   final count and prove fit before writing it. Dry-run inventory, CHUNK
   and EMBED_CHUNK use the same renderer and sizing check.
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
the same section owner, even across a child section. Select those slots in
the named grid for both E2 and the split grid's Selection and date-free
keys, including clipped converter-origin hashes and explicit empty slots.
Legacy E1 hashed only document-adjacent neighbours with the same owner.
When importing legacy results, compare the actual old and new consumed
slots using each occurrence's own grid and frozen inputs. If the legacy
hash encoded different slots, locate the frozen result by occurrence and
verify equivalence using the slots E2 consumed before carrying it forward.
Compare source text, relative origins, eligibility, stable header facts
and generations. Verify the frozen typed location elements and carried
context E2 uses. Leave legacy rows and keys intact.
[E1 keys](../../src/rememberstack/workers/e1.py#L734-L771),
[E2 slots](../../src/rememberstack/spine/chunk_catalog.py#L373-L419).

Claimify keeps D122's ordered producers, the target plus its previous eight
document chunks, including empty Selection results. Compare their actual
Selection keys for legacy reuse. Use each producer's consumed slots to
remap its cards and supporting spans, together with the target's slots.
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

For a new fragment, also admit a date from an original unsplit occurrence.
Use its parent content identity and stable undated source facts to locate
candidates. Verify byte-identical parent text and compatible eligibility,
generations and relative converter origins, then map the fragment by its
parent-relative range. An ambiguous match supplies no date. Consult retained
original legacy occurrences and earlier grids first ingested with splits.
Repair-generated occurrences add no date candidates. This gives the same
answer whether the earlier version has been repaired or still serves its
legacy grid. Preserve the recorded origin and the claim's immutable
assertion time. [D140 origin rule](effective_time_and_section_references_design.md#5-reuse-across-versions-text-origin-time).

**Embedding.** Carry a vector only on exact content hash, full rendered-input
hash, policy and model matches with the required dimension. Check the exact
predecessor's vector by chunk ID before calling the provider, including a
saved P1 attestation whose spine stamp was interrupted. Verify the content
hash on the predecessor row and the input hash against its frozen D80 input
and the successor's rendered input. Read active and privately retained
attestations by ID, so retry and later versions can reuse a vector after
its grid leaves search. Derive each successor's D80 facts with the final
count and preserve typed location elements for E2.
[E1 recovery](../../src/rememberstack/workers/e1.py#L319-L368),
[commit order](../../src/rememberstack/workers/e1.py#L569-L572).

Delivery uses the split basis only for affected representations. E0, D80,
eligibility, date-free identity, extractor, reference and normalizer policy
generations keep their pins. Changed inputs incur model work under their
new identities. Retries recover committed products and batch checkpoints.

## 3. Selective migration and replay

### Dispatch and writes

The spine records one pending packing basis and the published basis for the
representation. A basis pins the representation, original block and
structure generations, frozen rendering inputs, packing generation and
embedding envelope. Fitting legacy grids keep their delivery identities.
Split CHUNK, EMBED_CHUNK, dependent work and barriers name the basis in their
delivery identity and load rows by it. Reuse of paid products follows §2.

A durable deployment activation fence protects the affected routes. Before
enabling split writes, close database writer admission for incompatible
workers and drain or terminate their sessions. Revoke the old worker
credential and issue writer access only to compatible workers. A deployment
with shared writer credentials rotates that credential at the fence.
Compatible claim and due-route predicates enforce the required worker
generation. Old queued work for unaffected bases stays runnable by compatible
workers. A component stamp in the deduplication key alone cannot enforce
admission. [Claim predicate](../../src/rememberstack/spine/work_ledger.py#L2176-L2192).

When reserving the pending basis, fence legacy jobs for the target grid from
further writes. Every write, enqueue and barrier completion checks the
expected pending or published basis, live lineage and version, and current
processing attempt with its lease in the same write transaction. Take the
D143 layer fence before the lineage fence where both apply. Provider calls
and artifact reads run outside those transactions. A late or revoked attempt
cannot publish its results. Archive and forget use the same lineage fence.

### Predecessor and replay

Dry-run inventory scans embed dead letters and live ready grids, including
served historical versions, with a bounded, resumable cursor. An existing
D162 publication supplies its predecessor basis directly. For legacy work,
locate the exact ordered chunk IDs using the representation, packing stamp
and durable CHUNK/EMBED_CHUNK evidence for that grid or failed job.
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
[sidecar loader](../../src/rememberstack/core/blockizer.py#L38-L54).

Replay of an oversized legacy embed dead letter enqueues CHUNK repair for
the same reading. Keep the failed ledger row linked to the replacement
processing ID. Repeated replay finds the same repair and retries its
failed stage against the same basis. Proven oversized ready grids use
the same path.

### Publication

Keep pending normalized bodies and vector attestations outside both live
`chunk_search` indexes. After all nonempty pieces have verified embeddings,
open basis-local E2 work as required by D143. Its layer fence governs E2
admission and draining of admitted work. Keep successor claims and
occurrences private to E2 and out of claim search until the completed
Selection and Claimify/grounding barrier. When E2 is required, the
predecessor serves passages and testimony through both barriers.

Under the lineage fence, verify completion and diff predecessor and
successor `chunk_claims` occurrences. For affected claim IDs, compute
current carriage after the swap from the successor and other eligible
published bases: live snapshot versions or the living lineage's served
version. A historical repair cannot retire a claim still carried by the
served version. Consider lost occurrences even when the claim originated
elsewhere. Preserve immutable origin coordinates. In the same transaction
publish the successor occurrences, append any D54 `reextracted` currency
events and update the cache, recount affected facts and record any
zero-support review and evidence-change delta. This is a transcription
change under D54. Complete the barrier and durably open the applicable tail
in the same commit. Subsequent reconciliation consumes that committed result
and resolves published occurrences, including for living supersession
on actual source changes. [D54 completion](evidence_lifecycle_design.md#5-reconciliation--one-flow-for-both-problems).

In that transaction, swap the published passage pointer and its active
`chunk_search` rows, moving retained predecessor vectors to private storage.
A sources-only repair switches passages at E1 completion. If it had claims
from an earlier enabled layer, preserve their published occurrence basis
until D143 admits and completes the successor's extraction. An omitted
layer has no empty transcription to publish. D143 tails and readiness
resolve the named basis. A new version still meets its normal first-serving
readiness gate before its passages enter the serving indexes. A pre-cutover
failure leaves the predecessor serving and a retry resumes durable staging.

Representations awaiting a verified cutover retain their legacy reads.
Use the published passage authority for P1 nomination and confirmation,
`memory_v1` chunk views, document search, reference links and adjacency
through SQL hydration in one read snapshot. Block links choose the first
published piece covering the block by source order. Character links choose
the containing piece. A superseded ID is invisible to live adjacency.
Historical evidence reads use their stored basis and immutable artifact
coordinates. Retained data follows D159 erasure.
[P1 nomination](../../src/rememberstack/adapters/postgres_p1.py#L891-L927),
[adjacency](../../src/rememberstack/surfaces/query_engine.py#L2882-L2917).

## 4. Provider rejection and retry

E1 checks the prepared strings before calling the provider. A proven excess
fails with `embedding.input_too_large` and points to chunk repair. Preserve
structured input and length errors before capacity retries. Bisect a
rejected batch to isolate single-input failures and checkpoint valid
siblings. A confirmed deterministic rejection fails the job with
`embedding.input_too_large` or `embedding.input_rejected`, according to the
evidence. These failures are non-retryable. Report chunk and representation
IDs, count or bound, model, endpoint and provider status.

Generic 429s, timeouts and upstream outages use the existing bounded
capacity retries. An unusable singleton response fails the affected work.
Every nonempty input admitted under D162 requires a valid vector, including
fitting legacy-layout grids. Only a D80 empty body can complete with a
skip. Replay after a qualified envelope change uses the targeted repair
path. Query, claim, profile and fact embeddings use their existing
policies. [Current E1 rejection path](../../src/rememberstack/workers/e1.py#L393-L513).

## 5. Retrieval, release gates and rollout

### Benchmark admission

Protect both the LoCoMo and BEAM benchmark deployments with a durable
admission guard checked by ingest, inventory and replay. It blocks split
admission and repair of the protected corpus. Both profiles must pass the
following gates before release enables the changed path. Bind qualification
to each frozen manifest, grid and exact provider binding. Keep the retained
baselines read-only.

- **Frozen input and grid.** Verify the exact prepared readings, including
  LoCoMo's frozen OCR and BEAM's retained rendering. Dry-run the candidate
  packer under the qualified envelope. Ordered spans, bodies, full input
  hashes and eligibility must match the legacy grid. The retained stores
  must keep their published grid digests and IDs, with zero
  ingest-model calls and zero repair writes during qualification.
- **Ordered results and evidence.** Replay frozen requests against baseline
  and candidate readers on copies of the retained LoCoMo and BEAM stores
  with the same filters, query vectors and planner settings. Both must
  return the same ordered result IDs for passage, document, claim and fact
  reads and the same evidence occurrences, quoted spans and adjacency
  order. A mismatch blocks admission for that profile. A scorer smoke test
  cannot certify this gate.
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
| Packing and provider conformance | Fitting output is byte-identical across converter routes. Test at/below/above the ceiling, including an inconclusive upper bound for a fitting string, header-only overflow, dense YAML, OCR tables, a long turn and Unicode scalar fallback. Verify exact raw-span recovery, eligibility, origins and anchor restart. On each allowed endpoint, prove full delivery including a minimal piece with framing. Check the incident's actual rendered sizes. |
| Reuse and source authority | Count embedding, Selection and Claimify calls across two dated versions and a repair. Test a parent split into two owner runs by a child, with the oversize elsewhere. Assert reuse of the actual unchanged slots. Check fragment origins and relative-date claims with legacy repair before and after ingest of the newer version. Test two legacy generations and an older sidecar. Ambiguity preserves existing reads. Recover an unstamped P1 vector by predecessor ID and reuse it again after cutover. |
| Fences and publication | Stop between embedding and extraction. Reject writes from an in-flight legacy writer, an old worker restart and a superseded attempt. Check retries, archive/forget and D143 layer modes. Repair historical A that originated a claim carried by B, then exercise loss of the last eligible occurrence. Compare grid, currency, counts and review state at each barrier. Pending storage keeps ordered HNSW/BM25 results and scores, reference links and adjacency identical until cutover. Run D142's indexed-vs-exact recall, plan and latency gates under existing settings on a mixed corpus. [D142 checks](../../benchmarks/p1_ranked/README.md) |
| Error handling | Genuine 429s retry, deterministic rejection is typed and successful siblings are retained. Malformed singleton output blocks both fitting and split grids. |

Apply additive schema and compatible readers per D160. Activate workers
through §3's fence, run dry inventory, then canary the two affected hosted
documents. Check source coverage, ordered retrieval and paid-call deltas
before bounded expansion. Rollback pauses admission, keeps the worker fence
active and serves published bases with a compatible reader.
