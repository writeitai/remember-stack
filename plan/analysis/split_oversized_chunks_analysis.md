# Oversized E1 chunks in v0.22.4

Source baseline: `v0.22.4` (`c1f15b783`). This audit supports the proposed
[E1 size contract](../designs/split_oversized_chunks_design.md).

## Incident and failure path

The owner reported two failing documents from a 40-document hosted WriteIt
Project batch. Both used converter `text`. A roughly 4,000-line infra YAML file
and a Markdown decision log yielded chunks of 71,710 / 43,083 / 36,201 / 34,452
characters with respective `token_count` values of 3,021 / 4,086 / 2,902 /
5,395. The other 38 documents peaked at 765 whitespace tokens and 6,079
characters. E1's delivery stamp was
`e1-embed-2026.10:e1-chunker-2026.10a:whitespace-tokens:anchored:owner-runs:blockizer-heading-metadata:eligibility-cuts:source-origins-1`.

On `qwen/qwen3-embedding-8b` via OpenRouter pinned to `nebius/bf16`, the owner's
live probe got HTTP 200 for one 52,000-character input (19,960 provider tokens)
and for four 30,000-character inputs (45,919 tokens combined). Isolated 60,000-
and 72,000-character inputs consistently returned 429 with the generic Qwen
rate-limit message. The evidence points to a per-input boundary around 20k
tokens. The exact boundary and the production chunks' rendered input sizes
were unmeasured. E1 normally sends up to 64 inputs, and the adapter retries
429s as capacity errors. E1's outage test then re-raises, and the ledger spends
its attempt budget. Replaying the same embed row reuses the same cut and input.
[workers/e1.py:100–113](../../src/rememberstack/workers/e1.py#L100-L113),
[adapters/openrouter.py:1034–1123](../../src/rememberstack/adapters/openrouter.py#L1034-L1123),
[adapters/openrouter.py:1302–1360](../../src/rememberstack/adapters/openrouter.py#L1302-L1360),
[workers/e1.py:444–500](../../src/rememberstack/workers/e1.py#L444-L500),
[workers/e1.py:610–625](../../src/rememberstack/workers/e1.py#L610-L625),
[workers/base.py:507–579](../../src/rememberstack/workers/base.py#L507-L579).

## Where a chunk exceeds a limit

`ChunkHandler` is the only production caller of `pack_blocks` and writer of
`ChunkRecord`s. Every successful converter reading passes through E0's shared
`blockize`, committed `document.md` / `blocks.json`, and a section tree before
CHUNK. A parked conversion has no reading to chunk. A zero-block reading
returns an empty grid. Queue replay can also return old oversized rows without
invoking the packer. [workers/e0.py:969–988](../../src/rememberstack/workers/e0.py#L969-L988),
[workers/e0.py:1659–1718](../../src/rememberstack/workers/e0.py#L1659-L1718),
[workers/e0.py:2185–2200](../../src/rememberstack/workers/e0.py#L2185-L2200),
[workers/e1.py:132–191](../../src/rememberstack/workers/e1.py#L132-L191).

| Path | Why the current limit can be exceeded |
| --- | --- |
| One large block | A paragraph, heading, nested list item, table, quote, fence or indented code block retains the parser's full line range. The packer appends it and explicitly flushes it as a singleton even above 400 whitespace tokens. Both ordinary flush and final flush can emit a large span. [core/blockizer.py:128–192](../../src/rememberstack/core/blockizer.py#L128-L192), [core/blockizer.py:205–235](../../src/rememberstack/core/blockizer.py#L205-L235), [core/chunker.py:198–240](../../src/rememberstack/core/chunker.py#L198-L240) |
| A run of small blocks | The target counts `len(block_text.split())` and sums it per block. Long identifiers, URLs, punctuation, non-space-delimited languages and code can use many model tokens per whitespace token. The actual chunk slice also contains the text *between* its first and last blocks, which the sum excludes. The parser can skip tokens such as an HTML block between mapped blocks. [core/chunker.py:173–175](../../src/rememberstack/core/chunker.py#L173-L175), [core/chunker.py:198–238](../../src/rememberstack/core/chunker.py#L198-L238), [core/blockizer.py:177–193](../../src/rememberstack/core/blockizer.py#L177-L193) |
| Owners, anchors and sections | The packer starts a fresh run for each contiguous deepest section owner, including a parent's direct material between children. Anchors flush before a hashed block after a minimum gap. Owner and eligibility cuts only shorten runs. Parsed or proposed section boundaries land on whole blocks. A coarse root, large PDF page or merged conversational leaf can still own an arbitrarily large block. [core/chunker.py:93–132](../../src/rememberstack/core/chunker.py#L93-L132), [core/chunker.py:221–238](../../src/rememberstack/core/chunker.py#L221-L238), [core/structure_skeleton.py:97–140](../../src/rememberstack/core/structure_skeleton.py#L97-L140), [core/structure_skeleton.py:294–325](../../src/rememberstack/core/structure_skeleton.py#L294-L325), [core/section_snap.py:191–203](../../src/rememberstack/core/section_snap.py#L191-L203) |
| Final embedding input | D80 collapses whitespace, strips the body and may add a header and two newlines. It bounds only the header, at 48 code points. The size of a raw YAML span includes indentation that D80 collapses. Its whitespace count still says little about model tokens after rendering. Even a fitting body can overflow when the header is added. [core/embedding_input_policy.py:16–22](../../src/rememberstack/core/embedding_input_policy.py#L16-L22), [core/embedding_input_policy.py:104–116](../../src/rememberstack/core/embedding_input_policy.py#L104-L116), [core/embedding_input_policy.py:188–234](../../src/rememberstack/core/embedding_input_policy.py#L188-L234), [core/embedding_input_policy.py:338–351](../../src/rememberstack/core/embedding_input_policy.py#L338-L351), [workers/e1.py:290–295](../../src/rememberstack/workers/e1.py#L290-L295) |

| Source routes | Inputs to the same paths above |
| --- | --- |
| Text, Markdown, logs, source code and YAML/config | `text` returns the full UTF-8 reading up to 1 MB, including long lines and indentation. Its larger-file head/tail profile has fenced regions of up to 50 or 20 lines at 500 characters each. The passthrough route also produces a Markdown reading. Search-only code/config/log ranges select model-free, flat-root E0 structuring. E1 cuts at block eligibility transitions, and E2 records ineligible decisions without model calls. [Text limits](../../src/rememberstack/adapters/converters/text.py#L26-L32), [text converter](../../src/rememberstack/adapters/converters/text.py#L56-L149), [E0 dispatch](../../src/rememberstack/workers/e0.py#L1429-L1438), [flat root](../../src/rememberstack/workers/e0.py#L1659-L1681), [E1 cuts](../../src/rememberstack/core/chunker.py#L221-L240), [E2 eligibility](../../src/rememberstack/workers/e2.py#L524-L553) |
| Conversation, email and transcripts | Packaging produces text with message or turn boundaries when the source provides them. A single turn, pasted code block, quoted email or inline image reading can still yield a giant block. In fallback section trees, D137 merges runs of single-block leaves into the following leaf when available and coalesces them in place otherwise. The block grid stays intact. [benchmarks/locomo/protocol.py:581–629](../../benchmarks/locomo/protocol.py#L581-L629), [adapters/converters/email_message.py:65–110](../../src/rememberstack/adapters/converters/email_message.py#L65-L110), [fallback sections](../../src/rememberstack/core/structure_skeleton.py#L640-L703) |
| PDF, OCR, image, Office, HTML/EPUB and notebooks | PDF verifies the OCR pages and retains their Markdown and locators. OCR paragraphs and tables can be large. The other converters can emit the same large block shapes, including notebook code cells and image readings. Page, slide and cell bounds govern conversion or source coordinates. They are independent of a chunk's embedding size. [adapters/converters/pdf.py:112–152](../../src/rememberstack/adapters/converters/pdf.py#L112-L152), [adapters/converters/mistral_ocr.py:478–535](../../src/rememberstack/adapters/converters/mistral_ocr.py#L478-L535), [adapters/converters/office.py:143–158](../../src/rememberstack/adapters/converters/office.py#L143-L158), [adapters/converters/notebook.py:63–95](../../src/rememberstack/adapters/converters/notebook.py#L63-L95), [adapters/converters/__init__.py:147–187](../../src/rememberstack/adapters/converters/__init__.py#L147-L187) |
| Data profiles, cards, media and custom converters | Spreadsheet, delimited and dataset profiles and archive/binary/media cards still yield Markdown. Profile samples and metadata can be lexically dense. Current converter limits reduce exposure without proving a provider input bound. Custom routes installed via the provider port also flow through E0 and E1. [core/format_registry.py:166–229](../../src/rememberstack/core/format_registry.py#L166-L229), [adapters/converters/profile.py:248–276](../../src/rememberstack/adapters/converters/profile.py#L248-L276), [profiles/selfhost.py:1646–1652](../../src/rememberstack/profiles/selfhost.py#L1646-L1652) |

## Which size measure and repair point

- A lower whitespace target leaves atomic blocks and dense single words intact.
  A character ceiling alone assumes a token ratio. A conservative 16 KiB byte
  ceiling is sufficient for the pinned byte-level tokenizer but would cut a
  46,833-byte OCR block in the published LoCoMo input. The repo's E0 summary
  budget also uses whitespace tokens and characters, and D80's length counter
  is for choosing a header. Neither is a model token estimator.
  [workers/e0_summary.py:46–68](../../src/rememberstack/workers/e0_summary.py#L46-L68),
  [core/embedding_input_policy.py:104–111](../../src/rememberstack/core/embedding_input_policy.py#L104-L111).
- The proposed measure uses the pinned Qwen tokenizer for the final D80 input.
  The tokenizer's NFC normalization and byte-level BPE give a fast upper bound
  from NFC UTF-8 bytes. Every BPE token consumes at least one byte, and the
  single-input template adds one end token. Count exactly when that bound
  cannot prove safety. A 16,384-token hosted starting cap leaves 3,576 tokens
  below the successful 19,960-token observation. A higher cap needs measured
  route acceptance. OpenRouter's endpoint listing on 2026-11-27 advertised
  a 40,960 context length and gave no `max_prompt_tokens` for `nebius/bf16`.
  Bare `nebius` selects the same advertised endpoint family and supplies no
  evidence of an independent higher limit. [Qwen tokenizer at 1d8ad4c](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json),
  [OpenRouter endpoint listing](https://openrouter.ai/api/v1/models/qwen/qwen3-embedding-8b/endpoints),
  [adapters/openrouter.py:1014–1028](../../src/rememberstack/adapters/openrouter.py#L1014-L1028).
- Re-blockizing E0 would alter section and provenance coordinates and re-key
  ordinary text. Editing requests in E1 would separate the vectors from their
  authoritative chunks. Splitting only offending outputs of the legacy packing
  pass keeps both the E0 substrate and existing good cuts. A fixed probe order
  makes the whitespace target and structural preferences reproducible even
  when extending a prefix changes its tokenization or D80 header.
- I ran an HTML gap with CRLF separators through the legacy blockizer,
  eligibility check and packer. The parser omitted the `code`-labelled `<div>`
  between two eligible paragraphs. The packer emitted one eligible candidate
  whose source span included the entire gap. Starting the split with the
  labelled eligibility runs preserves those bytes and keeps caps and anchors
  from crossing a label change.
  [Blockizer](../../src/rememberstack/core/blockizer.py#L177-L193),
  [validation](../../src/rememberstack/workers/e0.py#L969-L977),
  [packer](../../src/rememberstack/core/chunker.py#L198-L238).
- E1 currently batches by item count. A per-item proof says nothing about a
  gateway's request byte limit or an endpoint's total token budget. Qualifying
  whole requests before sending avoids using provider retries to discover
  batch limits. Existing bisection still isolates deterministic rejections.
  [Batch loop](../../src/rememberstack/workers/e1.py#L388-L410),
  [serialized request](../../src/rememberstack/adapters/openrouter.py#L1034-L1046),
  [capacity classifier](../../src/rememberstack/adapters/openrouter.py#L1302-L1343).
- A global `CHUNKER_VERSION` bump currently changes CHUNK, EMBED_CHUNK and the
  grid hard-coded in E2, E3 and readiness. Delivery deduplicates on target,
  stage and component version, so changing only the content hash or payload
  cannot reopen completed downstream work. The existing replay operation
  reopens the same row without changing the stage or component. An explicit
  representation-specific basis is needed for selective repair.
  [workers/e1.py:67–78](../../src/rememberstack/workers/e1.py#L67-L78),
  [profiles/selfhost.py:1837–1885](../../src/rememberstack/profiles/selfhost.py#L1837-L1885),
  [profiles/selfhost.py:1920–1969](../../src/rememberstack/profiles/selfhost.py#L1920-L1969),
  [spine/readiness.py:140–209](../../src/rememberstack/spine/readiness.py#L140-L209),
  [spine/work_ledger.py:1996–2023](../../src/rememberstack/spine/work_ledger.py#L1996-L2023),
  [spine/work_ledger.py:1026–1040](../../src/rememberstack/spine/work_ledger.py#L1026-L1040).

## What selective repair must preserve

- The ready chunk view joins on representation, so two packing generations can
  appear in one read. The sidecar loader recomputes stale grids, and E1's live
  source query uses the lineage's title. Repair needs a uniquely attested legacy
  grid and its historical coordinates and D80 facts. Guessing by recency can
  change both the repaired text and otherwise reusable provider inputs.
  [Ready view](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L717),
  [sidecar loader](../../src/rememberstack/core/blockizer.py#L38-L55),
  [E1 source query](../../src/rememberstack/spine/chunk_catalog.py#L270-L289).
- Legacy E1 hashes neighbours only when they are adjacent in document order.
  E2 takes the closest chunks with the same section path, including across a
  child section. Compare the actual slots across either direction of a layout
  change. A whole-parent hash misses an unchanged span after a local edit.
  Exact slice bytes and relative origins in the original parent supply date
  evidence even when the parent changed or was repacked.
  [E1 keys](../../src/rememberstack/workers/e1.py#L734-L771),
  [E2 windows](../../src/rememberstack/spine/chunk_catalog.py#L373-L419),
  [D140 lookup](../../src/rememberstack/spine/chunk_catalog.py#L332-L345).
- A P1 commit can survive an interrupted spine stamp. Recovery can verify the
  requested-input hash by exact chunk ID. The stamp covers the request, so it
  cannot establish full delivery. Historical binding provenance can supply
  that proof where it covers the original call. Keeping retired vectors in
  `chunk_search` would put them in broad HNSW and BM25 nomination. Private
  storage keeps them available for reuse without changing the index population.
  [E1 recovery](../../src/rememberstack/workers/e1.py#L319-L368),
  [commit order](../../src/rememberstack/workers/e1.py#L569-L572),
  [P1 nomination](../../src/rememberstack/adapters/postgres_p1.py#L891-L930).
- Appending a dense paragraph to a short root paragraph produced a single
  legacy candidate. I verified that cutting at the seam gives the original
  whole block and the same D80 hash, even with trailing CRLF and the final
  multi-chunk count. The current carry-forward query keys on the stored
  content and input hashes. A fragment tag would miss the old row.
  Deriving the run's key enables lookup once the run is preserved. The
  latter-half probe rule can miss the seam of a short unchanged block, so
  check fitting complete-block cuts before probing within a block.
  [Vector lookup](../../src/rememberstack/spine/chunk_catalog.py#L422-L440),
  [D80 input](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).
- The ledger's route predicate ignores component versions, so a new identity
  alone cannot fence old workers. Re-extraction also checks carriage only in
  the completing basis. E0 can serve B before its extraction finishes. D143
  checks the current source head before taking a supersession step. If B
  finishes claims and C takes the head with claims off, B's reconcile can
  return early forever. Settling B at its own ordered reconcile makes
  testimony and fact recount independent of the source-head update. The
  current SQL occurrence
  view follows live passages, while scoped hydration follows the representation
  alone. Each gives the wrong answer when the claim and passage grids differ.
  [Claim predicate](../../src/rememberstack/spine/work_ledger.py#L2176-L2192),
  [source head update](../../src/rememberstack/spine/document_catalog.py#L827-L852),
  [D143 guard](../../src/rememberstack/spine/lifecycle.py#L158-L197),
  [occurrence view](../../src/rememberstack/spine/migrations/versions/p9_31_0052_multi_span_claim_evidence.py#L22-L59),
  [scoped hydration](../../src/rememberstack/surfaces/query_engine.py#L6674-L6697),
  [re-extraction query](../../src/rememberstack/spine/lifecycle.py#L1449-L1475).
- Fresh claims default to current and E3 admission uses a matching entity
  unit. Applying that path to a repair of A after living version B settles
  would let a new A-only claim contribute fact support. Version deletion and
  archive recovery also accept origins or attachments on any grid. One
  completed publication predicate can govern automatic currency in both
  directions and keep the E3 tail from acting on historical repair outputs.
  [Claim default](../../src/rememberstack/spine/migrations/versions/p0_02_0004_claims_facts_evidence.py#L40-L46),
  [fact admission](../../src/rememberstack/spine/fact_applications.py#L345-L375),
  [removal and regain](../../src/rememberstack/spine/lifecycle.py#L1476-L1521),
  [archive recovery](../../src/rememberstack/spine/lifecycle.py#L1913-L1955).
- D140's interval union, fact fingerprint and validation watch join
  occurrences by representation. If A's repair drops a claim still carried
  by B, the old attachment can hold A's interval in the fact inputs even
  though the claim remains current and the support count stays the same.
  Queue the existing fenced review when edition carriage changes and use
  published claims for the period inputs. The fan-out can still use retired
  links to find the affected facts.
  [Intervals](../../src/rememberstack/spine/period_adjudication.py#L47-L85),
  [fingerprint](../../src/rememberstack/spine/period_adjudication.py#L129-L183),
  [validation](../../src/rememberstack/spine/period_adjudication.py#L426-L455),
  [review queue and scan](../../src/rememberstack/spine/period_adjudication.py#L561-L612).
- D90 stops at an existing fan-out row for the version and normalizer. D118
  uses the same scope to match staging with entity units. Basis-local
  membership lets a replacement finish its own tail with reused claim-level
  normalization and application receipts. The stream selects an admitted
  unapplied head even after its staging row disappears, so terminal
  membership retirement and release of an unused stream position both need
  durable transitions. A shared application can still have eligible work.
  [Fan-out guard](../../src/rememberstack/spine/work_ledger.py#L1538-L1547),
  [Stream admission and head](../../src/rememberstack/spine/fact_applications.py#L363-L414),
  [Completion](../../src/rememberstack/spine/fact_applications.py#L527-L540).
- D143 normally discharges unentered claims gaps on non-head living versions.
  That is appropriate for versions that never published claims. A
  sources-only repair can have an older claims basis still serving, so it
  owes that specific version a claims replacement when claims are enabled.
  [Discharge and admission](../../src/rememberstack/spine/processing_layers.py#L315-L354).
- The same head-only rule discharges unentered facts gaps. If B has entered
  claims, C can take the source head before B's facts boundary and leave
  B with a claims-only reconcile. If C then stalls, B stands without its
  facts. Keep the obligation on the eligible completed basis. Living
  versions that never entered claims can still be skipped, and genuine
  supersession can end a facts obligation that never entered while facts
  were off.
  [Discharge](../../src/rememberstack/spine/processing_layers.py#L897-L925),
  [Tail release](../../src/rememberstack/spine/processing_layers.py#L935-L995).
- `restore_support` records currency and recounts existing fact links. It
  writes no occurrence. Selecting only the new extraction basis would
  discard the reviewed quote and its D140 period while counting the claim's
  support. A verdict can authorize the exact earlier occurrence with its
  complete spans. Copying the occurrence into the new extraction result
  would incorrectly make it evidence of what that extractor produced.
  [Review candidate](../../src/rememberstack/spine/review.py#L87-L127),
  [Verdict](../../src/rememberstack/spine/review.py#L352-L402),
  [Period inputs](../../src/rememberstack/spine/period_adjudication.py#L47-L85).
- The D118 decision write checks attempt and input hash, independently of
  entity head selection. A worker that checks only current membership can
  miss a loss followed by restoration between its runs. Record a durable
  generation change at the loss, in the lineage transaction. The entity
  worker can release the old sequence asynchronously.
  [Decision compare-and-set](../../src/rememberstack/spine/fact_applications.py#L402-L443).
- A claim's primary citation normally names its immutable origin chunk.
  The adjacency query only accepts a live passage ID. The old chunk's range
  already determines its covering pieces on the new grid of the same
  reading, so the lookup can preserve the citation and use that range.
  [Evidence result](../../src/rememberstack/model/envelope.py#L336-L360),
  [Adjacency lookup](../../src/rememberstack/surfaces/query_engine.py#L6562-L6583).
- Dead-letter replay reopens the same job. On a first ingest there may be no
  published or failed legacy grid to replay. The pinned E0 inputs suffice
  to reproduce candidates. Committed vectors, Selection checkpoints and
  grounded Claimify outputs can carry across the new cuts where their input
  and provenance checks match.
  [Dead-letter replay](../../src/rememberstack/spine/work_ledger.py#L1026-L1040),
  [Selection freeze](../../src/rememberstack/spine/selection_catalog.py#L140-L201),
  [Grounded extraction commit](../../src/rememberstack/spine/claim_catalog.py#L303-L396).

## Serving rank and provider admission

- The pinned BM25 extension adds entries during insert and scores against
  physical index totals. Its abort hook does not remove ordinary inserted
  entries from those totals. A repeatable-read snapshot can still see the
  old rows with different scores. Filtering by published IDs affects row
  visibility only. Both native claim indexes are partial on currency, and
  the claim embedding worker presently follows fact adjudication or the
  claims-only boundary. Vector production and both replacement indexes must
  be ready before the currency and basis switch.
  [Image pin](../../Dockerfile.postgres#L1-L6),
  [Claim indexes](../../src/rememberstack/spine/migrations/versions/p9_13_0034_postgres_p1_search.py#L118-L132),
  [Claim embedding](../../src/rememberstack/workers/p1.py#L99-L144),
  [Index insertion](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/access/build.c#L2107-L2140),
  [Scoring totals](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/scoring/snapshot.c#L178-L204),
  [Abort hook](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/mod.c#L1428-L1438).
- A write freeze for the duration of a corpus copy makes progress depend on
  a lease outlasting two index builds. A private copy with durable change
  capture lets repairs build online. Keeping a caught-up spare costs a
  second projection per target and lets first publication add only its own
  rows. This preserves one index population per target for D142 scoring.
  A separate append index would require merging rankings across physical
  indexes with different BM25 statistics. A failed private index write also
  needs a clean private rebuild before selection because rollback alone can
  leave its physical totals changed.
- Stock Compose leaves embedding routing open. An order alone still permits
  marketplace fallback, even if a pin is also configured. Keeping stock
  routing on the compatibility path avoids silently sending data to a
  different provider. Qualify the effective closed binding for E1. The
  shared OpenRouter decoder sorts by index and checks count and dimensions.
  It accepts a singleton at index `1`, so using the strict E1 decoder for
  a public query would change which provider replies produce results. A
  correctly indexed 1,536-component zero vector also passes that decoder
  and fails the P1 validator. Check the mapping and every mapped vector
  with P1's validator before checkpointing the batch.
  [Compose settings](../../compose.yaml#L28-L36),
  [Routing and decode](../../src/rememberstack/adapters/openrouter.py#L1001-L1077),
  [Query embedding](../../src/rememberstack/surfaces/query_engine.py#L4838-L4866),
  [P1 validator](../../src/rememberstack/ports/p1_index.py#L26-L49).

## Benchmark fixture audit

LoCoMo runs the default packer (`400 / 24 / 200` for target / anchor modulus /
minimum anchor spacing) on one UTF-8 Markdown file per session. I verified the
pinned public dataset SHA-256 and checked all 10 samples, 272 sessions and 5,882
turns. The public text and captions without frozen image readings yielded 6,730
blocks. Across all 53,425 possible legacy spans the largest was 2,371 raw bytes
and the largest normalized body was 2,362 NFC UTF-8 bytes. The full input has
at most 3,133 Qwen tokens including header and end token for this text-only
projection.
[benchmarks/locomo/dataset.py:25–40](../../benchmarks/locomo/dataset.py#L25-L40),
[benchmarks/locomo/runner.py:558–567](../../benchmarks/locomo/runner.py#L558-L567),
[benchmarks/locomo/runner.py:1275–1317](../../benchmarks/locomo/runner.py#L1275-L1317),
[core/chunker.py:39–48](../../src/rememberstack/core/chunker.py#L39-L48),
[pinned LoCoMo source](https://github.com/snap-research/locomo/blob/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/data/locomo10.json).

The **published** v45 ingestion also includes frozen OCR/vision readings. I
read the archived v45 run `93c96407dd8a…` at ingestion revision `13ce4a522`,
verified its image-index SHA-256 (`26a9033ff7c9…`) and all 740 distinct reading
hashes referenced by its 910 attachment records, and reproduced the full
prepared-document manifest SHA-256
`04869f1b20ed5bf6bab6e5cd1fce639a770cc82fcbf2fc12790dcae22ad7c98c`.
This uses the original Markdown rendering, including recorded unavailable-image
notes. The fixture and stored inference were read only.
[benchmarks/locomo/README.md:20–51](../../benchmarks/locomo/README.md#L20-L51),
[benchmarks/locomo/protocol.py:581–629](../../benchmarks/locomo/protocol.py#L581-L629),
[benchmarks/locomo/images.py:335–387](../../benchmarks/locomo/images.py#L335-L387).

I blockized those 272 files with the locked `markdown-it-py==4.2.0` and
enumerated all 352,993 potential spans over
15,732 blocks. Every emitted legacy chunk is either a singleton or a
contiguous run whose block counts sum to at most 400. This superset covers
any selected sections, eligibility cuts and content anchors. After D80
normalization the largest body was 46,833 NFC UTF-8 bytes (`conv-49/D20`).
The maximum was **21,566 Qwen body tokens** without the end token, using
`tokenizers==0.22.2` and official tokenizer SHA-256 `83cdf8c3a34f…`.
`conv-41/D19` block 29 is a 21,779-byte single paragraph with 4,442 whitespace
tokens. It is emitted as a singleton regardless of structure and has **21,567
tokens with the end token before any header**. The 48-code-point D80 header
and separators contribute at most 770 tokens, using four canonical decomposition
scalars per code point and at most four UTF-8 bytes per scalar. The Qwen
pre-tokenizer separates the two newlines from the stripped body. Including the
end token, **all** v45 candidates fit within **22,337 tokens**. A qualified
22,528-token ceiling would therefore never split this corpus. At 16,384 tokens
the reported singleton necessarily splits on a fresh ingest. Route qualification
at 22,528 remains open.
[core/chunker.py:198–240](../../src/rememberstack/core/chunker.py#L198-L240),
[core/embedding_input_policy.py:16–22](../../src/rememberstack/core/embedding_input_policy.py#L16-L22),
[core/embedding_input_policy.py:208–222](../../src/rememberstack/core/embedding_input_policy.py#L208-L222),
[benchmarks/locomo/protocol.py:127–134](../../benchmarks/locomo/protocol.py#L127-L134),
[Qwen tokenizer at 1d8ad4c](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).

The BEAM repository fixture here is the 100K/1 scoring rubric. Its pinned
upstream chat has 3 batches, 78 turn groups and 188 messages. Rendering each
message as a role-labelled Markdown paragraph yields a worst-case 14,187
normalized body bytes across the same legacy span superset. With the header
and end token, the byte upper bound is 14,958 Qwen tokens. This probe covers
that rendering. The release gate audits the exact retained BEAM input and
grid. Importing upstream JSON wholesale would instead give a single
529,540-byte normalized body and is a different input.
[BEAM fixture provenance](../../benchmarks/rs_harness_beam/fixtures/PROVENANCE.md),
[upstream 100K/1 chat at 5a64c99](https://github.com/mohammadtavakoli78/BEAM/blob/5a64c992ba05b489b891221b7bcb6b763493dde5/chats/100K/1/chat.json).

Read-only storage protects the input corpus. The reader and SQL schema still
determine the tool responses. A query can return the same claim IDs and quotes
with different fact windows or scalar SQL values. Fixed-vector replay
isolates reader changes but bypasses query embedding. Pin the effective
query binding and run changed query paths through captured provider replies
at the transport boundary before switching a protected reader.
[LoCoMo public tool dispatch](../../benchmarks/locomo/retrieval.py#L165-L202),
[BEAM SQL retrieval](../../benchmarks/rs_harness_beam/answer_agent.py#L511-L525).
