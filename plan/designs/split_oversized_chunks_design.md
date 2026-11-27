# Split oversized chunks in E1 (D162)

**Status** Proposed. This is the binding contract on acceptance. The [v0.22.4
audit](../analysis/split_oversized_chunks_analysis.md) records the incident,
source paths and benchmark measurements.

The chunker partitions every oversized passage into bounded source slices.
Every slice gets its own chunk and embedding. E1 sends the entire prepared
string for each slice. An embedding failure leaves the work incomplete.
Documents whose existing chunks fit keep their rows, IDs, hashes and paid
products. This applies to every converter, including search-only inputs.

Today every converter reading reaches the same packer. It flushes a large
single block intact and counts a multi-block span by summing the blocks'
whitespace tokens. This omits inter-block text and token density. D80 adds
location text after packing. The result can exceed the provider limit
inside any section, regardless of the anchor layout.
[core/chunker.py:198–240](../../src/rememberstack/core/chunker.py#L198-L240),
[workers/e1.py:290–295](../../src/rememberstack/workers/e1.py#L290-L295).

## 1. Size contract and split points

The **packing target** remains the legacy 400 whitespace tokens with anchor
modulus 24 and minimum gap 200. The **hard ceiling** applies to the full D80
embedding string, including its location header, separators and tokenizer
special tokens. At composition, bind the tokenizer and ceiling to the
configured embedding model and allowed provider endpoints. Use the smallest
qualified ceiling when routing can select among endpoints. Pin the local
tokenizer implementation and data by version and checksum. Pack and validate
offline with that same counter. For a custom binding the provider port must
supply an attested counter or a conservative upper bound and a qualified
limit. Capability is deployment configuration, never a live response used to
choose cuts. [core/chunker.py:39–63](../../src/rememberstack/core/chunker.py#L39-L63),
[ports/model_provider.py:16–28](../../src/rememberstack/ports/model_provider.py#L16-L28),
[core/embedding_input_policy.py:188–234](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).

For the hosted Qwen/Nebius pin, start qualification at **16,384 Qwen tokens**.
When NFC UTF-8 byte count plus the end token fits, it proves the input fits.
Otherwise count exactly with the pinned Qwen tokenizer. The count leaves room
below the observed 19,960-token success for any verified upstream framing
overhead. The trial ceiling for a benchmark-compatible route is **22,528**,
subject to §5. These are measurement points. The qualified ceiling and
counter fingerprint are part of the split packing generation. The byte
shortcut affects only how the same decision is computed.

1. Run the existing whole-block packer with the committed section tree and
   extraction eligibility. For repair, start from the selected grid's
   recorded spans. Render the actual D80 input for each
   candidate. Return the legacy packer result verbatim when all candidates
   fit. Empty readings keep their existing empty-grid completion.
2. Refine only offending candidates, within their exact source bounds.
   Retain every original cut, including content anchors, section/owner runs
   and eligibility fences. Inside a candidate, prefer block boundaries and
   known source units such as page, turn, cell and table row boundaries.
   Within a block, prefer YAML document and sibling key/item boundaries
   outside block scalars, followed by line ends for code, YAML and tables.
   For prose, prefer sentence ends, then line ends. Use versioned lexical
   rules and recorded source layout. A newline or sentence mark adds no text.
3. Find a fitting prefix using a conservative bound on body, maximum header
   and framing. For split pieces use the 400-word target where it offers
   a fitting cut. Take the furthest boundary in the strongest tier within
   the latter half of that prefix. At an indivisible unit, fall through to
   whitespace and then a Unicode scalar boundary. Keep CRLF together and advance by at
   least one scalar. A huge word, table row or line is divisible. Delimiters
   and interior whitespace stay in the source slices, with whitespace-only
   runs attached to a neighbouring slice so the split creates no empty
   embedding bodies. Use the existing hash/modulus and minimum-gap rule on
   the natural right-hand units within an offending candidate to reset
   packing at accepted internal anchors. Cuts can move within an edited
   interval up to the next unchanged accepted anchor, or the candidate's end
   when there is no anchor. Work on bounded prefixes and reuse byte counts.
   Check accepted cuts with the exact renderer and counter so a BPE merge or
   a header-mode transition cannot invalidate a cheap size estimate.
4. Use multi-chunk header semantics when refining a failing singleton.
   The trigger is based on the legacy candidate and requires at least two
   pieces. D80 branches on one vs several, so other candidates of a
   multi-chunk reading keep their exact rendering. Re-render the completed
   grid with its actual chunk count and verify the ceiling before writing
   any row. The 48-code-point header cap leaves space for even the scalar
   fallback under a qualified limit. Both the planner and E1 use this shared
   rendering and sizing path. [core/chunker.py:93–132](../../src/rememberstack/core/chunker.py#L93-L132),
   [core/chunker.py:198–240](../../src/rememberstack/core/chunker.py#L198-L240),
   [core/embedding_input_policy.py:249–274](../../src/rememberstack/core/embedding_input_policy.py#L249-L274),
   [core/embedding_input_policy.py:338–351](../../src/rememberstack/core/embedding_input_policy.py#L338-L351).

## 2. Coordinates, evidence and identities

The immutable E0 reading, block grid and selected section generation remain
the coordinate authority. The concatenated half-open `char_start` and
`char_end` slices of each split parent recover its original text exactly,
in Python Unicode code points. Child `block_start` and `block_end` describe
the intersected E0 blocks. Several pieces can refer to the same block.
Associate a piece solely in an inter-block gap with the preceding block of
the same parent candidate and retain a passage for its exact slice. Apply
the parent eligibility to its blocks and unlabeled gaps, and use the same
D133 eligibility policy at any labelled gap transition. Section ownership,
role, title, page or turn locators and converter-origin labels come from
the original reading. Block-grained links choose the first published piece
by source offset. Character-grained links match the containing span.
[workers/e1.py:148–165](../../src/rememberstack/workers/e1.py#L148-L165),
[core/extraction_eligibility.py:50–71](../../src/rememberstack/core/extraction_eligibility.py#L50-L71),
[spine/document_references.py:1366–1381](../../src/rememberstack/spine/document_references.py#L1366-L1381).

- **Occurrence.** Persist a distinct packing generation only for split
  grids, with the legacy parameter stamp plus split algorithm, seam rules
  and limit fingerprint. Use the original representation and section IDs.
  Materialize the grid once with IDs derived from the immutable packing basis
  and the exact span (namespaced UUIDv5). Ordinal is the resulting document
  order and stays out of that ID. The selected structure generation and
  frozen rendering inputs are part of the basis. A new document version
  still gets its own occurrences. On a no-split replay, return existing
  UUIDs. Fresh no-split inserts still allocate UUIDs as v0.22.4 does.
  [core/chunker.py:51–63](../../src/rememberstack/core/chunker.py#L51-L63),
  [workers/e1.py:132–146](../../src/rememberstack/workers/e1.py#L132-L146),
  [workers/e1.py:678–730](../../src/rememberstack/workers/e1.py#L678-L730).
- **Content and extraction.** Retained whole candidates keep their legacy
  `chunk_content_hash`. New pieces hash their exact UTF-8 source slice with a
  fragment domain tag. Neither position nor the parent's content hash joins
  that payload. Use piece hashes and origin intersections in the existing
  `reuse_identity_hash` and Selection hash. Keep `REUSE_IDENTITY_VERSION`,
  the eligibility, extractor, reference and normalizer policy versions and
  all E0 generations. The split grid uses the same document-adjacent,
  same-owner slots for both E1 hashes and E2 inputs. The current E2 neighbour
  query can jump across a child run, so reuse from a legacy grid checks the
  actual consumed slots as well. Claimify keys on the resulting Selection
  hashes for the target and previous eight chunks. A split can change
  Selection on immediate same-section neighbours and Claimify on up to eight
  successors of each changed Selection input. [workers/e1.py:656–790](../../src/rememberstack/workers/e1.py#L656-L790),
  [core/text_origin.py:18–51](../../src/rememberstack/core/text_origin.py#L18-L51),
  [spine/chunk_catalog.py:379–419](../../src/rememberstack/spine/chunk_catalog.py#L379-L419),
  [workers/e2.py:592–605](../../src/rememberstack/workers/e2.py#L592-L605).
- **Text origin and claim provenance.** In a same-version repair, inherit
  the established D140 date (including unknown) of the exact old parent
  occurrence for all its unchanged bytes. Ordinary earlier-version lookup
  uses the existing date eligibility rule. Clip source passages and
  derivation ranges to each target and admitted neighbour, with absolute
  evidence offsets and relative origin hashes. A claim can cite adjacent
  pieces via D119 multi-span evidence. For unchanged windows, permit
  same-version reuse only from the selected predecessor's exact occurrence
  with matching keys, windows and checked substrings. Use the prior chunk's
  own generation when loading its windows. Current prior queries are
  earlier-version-only and E2 loads prior windows by its configured grid.
  A changed or ambiguous window runs normal extraction and reconciliation.
  Existing claim IDs and evidence spans stay immutable. [core/text_origin.py:54–85](../../src/rememberstack/core/text_origin.py#L54-L85),
  [core/source_origins.py:30–66](../../src/rememberstack/core/source_origins.py#L30-L66),
  [core/source_passages.py:109–171](../../src/rememberstack/core/source_passages.py#L109-L171),
  [core/source_passages.py:273–313](../../src/rememberstack/core/source_passages.py#L273-L313),
  [spine/selection_catalog.py:102–125](../../src/rememberstack/spine/selection_catalog.py#L102-L125),
  [spine/claim_catalog.py:425–446](../../src/rememberstack/spine/claim_catalog.py#L425-L446),
  [workers/e2.py:1148–1186](../../src/rememberstack/workers/e2.py#L1148-L1186).
- **Embedding.** Recompute source, section and message location facts per
  new occurrence with the final count. Derive `context_prefix` from its D80
  `location_header`, and keep the full typed location elements for E2. Hash
  the exact transmitted string in `embedding_text_hash`. The rendering,
  embedding policy generation, embedder identity and vector dimension stay
  as configured. Carry vectors only under existing exact content/input/
  policy/embedder matches. A changed input pays for a new vector. IDs and
  offsets never enter the embedding string. [workers/e1.py:281–295](../../src/rememberstack/workers/e1.py#L281-L295),
  [workers/e1.py:319–368](../../src/rememberstack/workers/e1.py#L319-L368),
  [workers/e1.py:551–567](../../src/rememberstack/workers/e1.py#L551-L567),
  [workers/e1.py:628–653](../../src/rememberstack/workers/e1.py#L628-L653).

## 3. Selective migration and replay

Use one selected chunk grid per representation. Keep the legacy
`CHUNKER_VERSION` and CHUNK and EMBED_CHUNK delivery identities for legacy
grids. Store a pending and a published basis for split grids in the spine.
The basis pins the representation, structure generation, packing generation
and the embedding envelope. Record the pending rows and their follow-up
idempotently under the representation/lineage fence. Find or create this
basis and its rows once. The split CHUNK/EMBED_CHUNK component versions and
dependent jobs and barriers use the split basis as part of **delivery**
identity. A completed old job can never satisfy a new basis through a
payload-only change. Stage handlers, claim fan-out, D143 gates, reconciliation
and readiness resolve the named basis instead of a process-wide default or
the newest rows. Preserve the old advertised stage IDs and readiness for
untouched grids. [workers/e0.py:2185–2200](../../src/rememberstack/workers/e0.py#L2185-L2200),
[workers/e1.py:67–78](../../src/rememberstack/workers/e1.py#L67-L78),
[spine/work_ledger.py:1996–2023](../../src/rememberstack/spine/work_ledger.py#L1996-L2023),
[spine/readiness.py:140–209](../../src/rememberstack/spine/readiness.py#L140-L209),
[spine/processing_layers.py:1309–1331](../../src/rememberstack/spine/processing_layers.py#L1309-L1331).

1. **Inventory.** In the engine's operator surface, dry-run and then repair
   scoped deployments with a bounded, resumable cursor. Examine suspect
   `embed_chunk` dead letters and existing live ready grids, including
   served historical versions. Use exact stored spans and D80 inputs from
   the persisted reading. Cheap size bounds may rule out safe rows. Unknown
   sizes require reading their artifacts, with one read per candidate
   representation and no provider calls. Revalidate the selected basis before
   enqueueing after object reads. Audit safe documents without
   changing them or queuing work. Leave retained benchmark snapshots read
   only. Archive/forget fences govern eligibility and publication.
2. **Recover.** A replay of an oversized legacy embed dead letter means
   enqueue CHUNK repair for the same version/representation under the new
   basis, then embed the resulting grid. Keep the old failed ledger row
   and link it to the replacement processing ID. The current replay
   transition reopens the same stage and component, so calling it blindly
   would send the same oversized input. Repeat replay calls find the same
   repair, or replay its failed stage with the same inputs after a transient
   failure clears. Already stored successful oversize rows in scope follow
   the same repair path and keep serving until the replacement is ready.
   Reuse the immutable E0 artifacts. [spine/work_ledger.py:1026–1098](../../src/rememberstack/spine/work_ledger.py#L1026-L1098),
   [workers/operations.py:20–42](../../src/rememberstack/workers/operations.py#L20-L42).
3. **Publish.** Stage successor vectors and P1 rows under the pending basis.
   Once all its nonempty pieces have verified embeddings, atomically switch
   the published chunk grid, replace its active search projection and durably
   open eligible E2 work against it. Successful staged vectors remain
   available to retries until the switch.
   Shared P1 nomination and confirmation, `memory_v1` chunk views and
   `adjacent_chunks` select the published grid in the same snapshot.
   Unfinished work and old grid vectors stay out of ranked search. Retain
   old rows and source coordinates for historical evidence and verified
   reuse. Adjacent reads of a superseded ID must never mix grids. Ordinary
   E2/E3 barriers and reconciliation maintain claim currency. A same-version
   repair is a transcription change, so loss of claim support is reviewed
   under the re-extraction rule rather than treated as a source edit.
   A stale worker can neither publish an obsolete basis nor revive forgotten
   data. [surfaces/query_engine.py:6562–6583](../../src/rememberstack/surfaces/query_engine.py#L6562-L6583),
   [adapters/postgres_p1.py:1323–1333](../../src/rememberstack/adapters/postgres_p1.py#L1323-L1333),
   [workers/reconcile.py:153–165](../../src/rememberstack/workers/reconcile.py#L153-L165),
   [workers/reconcile.py:240–249](../../src/rememberstack/workers/reconcile.py#L240-L249).

## 4. Provider rejection and retry

E1 checks the exact prepared strings against the selected envelope before
provider calls. A stale oversized input fails closed with a typed
`embedding.input_too_large` error and a link to chunk repair. The adapter
preserves structured input/context-length failures before capacity retries.
For an input-size or request-size failure on a batch, bisect the **batch**
and checkpoint successful calls under the existing accounting rules. A
confirmed deterministic single-input rejection ends the affected embedding
job as non-retryable with `embedding.input_too_large` or
`embedding.input_rejected` according to the evidence. Report the stable code,
chunk/representation IDs, bound
or token count, model/endpoint and provider status. The stored error and
readiness show the rejection. Keep the rejected chunk unstamped and its
replacement grid unpublished. A malformed singleton response on a split
grid also cannot close a nonempty piece as `skip:poison_chunk`.
[adapters/openrouter.py:1034–1123](../../src/rememberstack/adapters/openrouter.py#L1034-L1123),
[workers/e1.py:393–442](../../src/rememberstack/workers/e1.py#L393-L442),
[workers/e1.py:444–513](../../src/rememberstack/workers/e1.py#L444-L513),
[workers/base.py:507–530](../../src/rememberstack/workers/base.py#L507-L530).

A generic 429, including the observed Nebius text, is insufficient to
identify a size failure. Standard throttling, timeouts and upstream outages
retain bounded retry/backoff and report exhaustion as a capacity failure.
A structured size signal or a locally proven breach of the qualified
endpoint envelope is deterministic. Replay after a verified provider limit
change binds a new envelope and runs targeted repair under it.
Existing empty-body completion retains its D80 meaning. These rules govern
passage ingest. Query, claim, profile and fact embedding requests retain
their current preparation and behavior.

## 5. Retrieval, release gates and rollout

The serving contract retains the existing retrieval operations, embedding
model and dimension, semantic and BM25 scoring, rank fusion, document/time/
tag/role filters, claim and fact semantics, and original-source reads. The
published-grid check prevents mixed old/new passages. For a split reading,
passage ranking and adjacency can change and newly extracted claims can
change its evidence set. Other readings have identical stored inputs and
products. Their ranks can move in a mixed search when the competing candidates
change. [surfaces/query_engine.py:2736–2800](../../src/rememberstack/surfaces/query_engine.py#L2736-L2800),
[surfaces/query_engine.py:2861–2929](../../src/rememberstack/surfaces/query_engine.py#L2861-L2929).

**LoCoMo.** The published **94.09% (1,449/1,540)** is a score of saved
answers, grades and an attested v45 ingestion. The repair neither rewrites
that corpus nor regenerates its answers or labels. A fresh stochastic run
has sampling variation even with unchanged inputs. The audited public text
fits the proposed hosted starting cap. The actual frozen vision/OCR reading
contains a **21,567-token singleton before its header**, so a fresh v45
ingest under 16,384 would change the grid. Every possible v45 legacy input
fits within **22,337 tokens** with the header and end token. Qualifying
22,528 on the pinned benchmark binding proves zero fresh splits. A lower
qualified ceiling requires an exact audit of the full D80 inputs. Qualify
route acceptance independently of the fixture-size proof.
[Benchmark measurements](../analysis/split_oversized_chunks_analysis.md#benchmark-fixture-audit),
[published score](../../benchmarks/locomo/README.md#published-results).

| Release gate | Pass condition |
| --- | --- |
| Packer and renderer unit/golden | Byte-for-byte legacy output for all fitting routes, including singleton/multi header modes and below/at/above limits. Reproduce indentation-heavy YAML with dense keys and values that overflow after D80 normalization, the long Markdown log, a giant no-break word/line with multibyte Unicode, PDF/OCR tables, code fences, a long dialogue turn, eligibility transitions and a header-only overage. Assert full raw-span coverage, no overlap, exact provider size, order, origin labels and anchor resynchronization. |
| E1/E2 and PostgreSQL integration | Count embedding, Selection and Claimify calls for two dated versions and an in-place repair. Compare untouched rows, claim IDs, spans, dates, vectors and readiness. Exercise D122's window, same-section boundaries, disabled claims/facts, successful legacy rows, failed replay, crash/redelivery, concurrent repair and archive/forget. Verify true 429 retry, deterministic invalid input, batch isolation and atomic publication. [Existing chain](../../src/tests/workers/test_e1_chain.py), [reuse lifecycle](../../src/tests/workers/test_reuse_lifecycle.py), [D140 PG](../../src/tests/workers/test_d140_text_origin_pg.py), [D119 reuse](../../src/tests/workers/test_d119_reuse.py), [adapter tests](../../src/tests/adapters/test_openrouter.py) |
| Benchmark and read-plane parity | Audit exact frozen LoCoMo and BEAM inputs and active grid digests with zero edits and zero ingest-model calls. On retained copies, run LoCoMo smoke and publication retrieval/attestation, including ordered chunk/adjacency and claim/fact evidence checks against the baseline. Run BEAM 100K/1 retrieval and scorer smoke against its retained store. Qualify the benchmark binding and require zero fresh-cut changes on the frozen fixtures. Run D142's indexed-vs-exact recall and P1 query plan/latency gates with a mixed grid corpus. If the reader evidence differs, require the paired LoCoMo non-inferiority gate on identical retained ingestion, with the documented approval for paid comparisons. [LoCoMo gates](locomo_benchmark_design.md), [D142 harnesses](../../benchmarks/p1_ranked/README.md) |
| Hosted conformance | On the pinned route, verify full unmodified inputs at and near the limit using punctuation-dense, Unicode and production-shaped blocks. Show all generated pieces accepted under valid capacity. Check actual rendered sizes of the incident chunks to verify the repair selector. Enforce both the provider-safe ceiling and fresh benchmark parity before release. |

Roll out additive schema and compatible readers/workers per D160 before
enabling split writes. Run a dry inventory, then canary the two affected
hosted documents by version/representation. Compare source coverage,
terminal errors, chapter/turn retrieval and paid-call deltas. Expand in
bounded batches once the metrics match. A rollback pauses split admission
and preserves published bases with a compatible reader. Pending failures
leave the previous published grid in place.

Open questions for qualification are the route's effective single-input
limit and exact 429 error metadata, which qualified endpoint can serve the
22,528-token benchmark envelope under the pinned protocol, and the observed
migration volume and retrieval quality of splits on real YAML, OCR and
long Markdown. If the benchmark binding cannot qualify a fitting ceiling,
fresh benchmark parity is a release blocker. The saved 94.09% and its
retained store still identify the published run.
