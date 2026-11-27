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
| Owners, anchors and sections | The packer starts a fresh run for each contiguous deepest section owner, including a parent's direct material between children. Anchors flush before a hashed block after a minimum gap. Owner and eligibility cuts only shorten runs. Parsed or proposed section boundaries land on whole blocks. A coarse root, large PDF page or merged conversational leaf can still own an arbitrarily large block. [core/chunker.py:93–132](../../src/rememberstack/core/chunker.py#L93-L132), [core/chunker.py:221–238](../../src/rememberstack/core/chunker.py#L221-L238), [core/structure_skeleton.py:97–140](../../src/rememberstack/core/structure_skeleton.py#L97-L140), [core/structure_skeleton.py:294–325](../../src/rememberstack/core/structure_skeleton.py#L294-L325), [core/section_snap.py:191](../../src/rememberstack/core/section_snap.py#L191) |
| Final embedding input | D80 collapses whitespace, strips the body and may add a header and two newlines. It bounds only the header, at 48 code points. The size of a raw YAML span includes indentation that D80 collapses. Its whitespace count still says little about model tokens after rendering. Even a fitting body can overflow when the header is added. [core/embedding_input_policy.py:16–22](../../src/rememberstack/core/embedding_input_policy.py#L16-L22), [core/embedding_input_policy.py:104–116](../../src/rememberstack/core/embedding_input_policy.py#L104-L116), [core/embedding_input_policy.py:188–234](../../src/rememberstack/core/embedding_input_policy.py#L188-L234), [core/embedding_input_policy.py:338–351](../../src/rememberstack/core/embedding_input_policy.py#L338-L351), [workers/e1.py:290–295](../../src/rememberstack/workers/e1.py#L290-L295) |

| Source routes | Inputs to the same paths above |
| --- | --- |
| Text, Markdown, logs, source code and YAML/config | `text` returns the full UTF-8 reading up to 1 MB, including long lines and indentation. Its larger-file head/tail profile has fenced regions of up to 50 or 20 lines at 500 characters each. The passthrough route also produces a Markdown reading. Search-only code/config/log ranges select model-free, flat-root E0 structuring. E1 cuts at block eligibility transitions, and E2 records ineligible decisions without model calls. [Text converter](../../src/rememberstack/adapters/converters/text.py#L56-L149), [E0 dispatch](../../src/rememberstack/workers/e0.py#L1429-L1438), [flat root](../../src/rememberstack/workers/e0.py#L1659-L1681), [E1 cuts](../../src/rememberstack/core/chunker.py#L221-L240), [E2 eligibility](../../src/rememberstack/workers/e2.py#L524-L553) |
| Conversation, email and transcripts | Packaging produces text with message or turn boundaries when the source provides them. A single turn, pasted code block, quoted email or inline image reading can still yield a giant block. D137 merges single-block fallback sections forward to preserve context and leaves the block grid intact. [benchmarks/locomo/protocol.py:581–629](../../benchmarks/locomo/protocol.py#L581-L629), [adapters/converters/email_message.py:65–110](../../src/rememberstack/adapters/converters/email_message.py#L65-L110), [core/structure_skeleton.py:665–703](../../src/rememberstack/core/structure_skeleton.py#L665-L703) |
| PDF, OCR, image, Office, HTML/EPUB and notebooks | PDF verifies the OCR pages and retains their Markdown and locators. OCR paragraphs and tables can be large. The other converters can emit the same large block shapes, including notebook code cells and image readings. Page, slide and cell bounds govern conversion or source coordinates. They are independent of a chunk's embedding size. [adapters/converters/pdf.py:112–152](../../src/rememberstack/adapters/converters/pdf.py#L112-L152), [adapters/converters/mistral_ocr.py:478–535](../../src/rememberstack/adapters/converters/mistral_ocr.py#L478-L535), [adapters/converters/office.py:143–158](../../src/rememberstack/adapters/converters/office.py#L143-L158), [adapters/converters/notebook.py:63–95](../../src/rememberstack/adapters/converters/notebook.py#L63-L95), [adapters/converters/__init__.py:147–187](../../src/rememberstack/adapters/converters/__init__.py#L147-L187) |
| Data profiles, cards, media and custom converters | Spreadsheet, delimited and dataset profiles and archive/binary/media cards still yield Markdown. Profile samples and metadata can be lexically dense. Current converter limits reduce exposure without proving a provider input bound. Custom routes installed via the provider port also flow through E0 and E1. [core/format_registry.py:166–229](../../src/rememberstack/core/format_registry.py#L166-L229), [adapters/converters/profile.py:248](../../src/rememberstack/adapters/converters/profile.py#L248), [profiles/selfhost.py:1646–1652](../../src/rememberstack/profiles/selfhost.py#L1646-L1652) |

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
  pass keeps both the E0 substrate and existing good cuts. Request batch
  bisection isolates provider failures independently of the source split.
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
  [sidecar loader](../../src/rememberstack/core/blockizer.py#L38-L54),
  [E1 source query](../../src/rememberstack/spine/chunk_catalog.py#L270-L289).
- Legacy E1 hashes neighbours only when they are adjacent in document order.
  E2 takes the closest chunks with the same section path, including across a
  child section. The actual slots can be compared against the frozen work to
  reuse it under a corrected key. Date lookup by the fragment key alone cannot
  find its legacy parent. The earlier parent's unchanged bytes and committed
  date supply that evidence before and after a repair.
  [E1 keys](../../src/rememberstack/workers/e1.py#L734-L771),
  [E2 windows](../../src/rememberstack/spine/chunk_catalog.py#L373-L419),
  [D140 lookup](../../src/rememberstack/spine/chunk_catalog.py#L332-L345).
- A P1 commit can survive an interrupted spine stamp. Recovery can read the
  vector and verify its hash by exact chunk ID. Keeping retired vectors in
  `chunk_search` would put them in broad HNSW and BM25 nomination before the
  ready-view join. A private store supports recovery and reuse during staging
  without changing the index population.
  [E1 recovery](../../src/rememberstack/workers/e1.py#L319-L368),
  [commit order](../../src/rememberstack/workers/e1.py#L569-L572),
  [P1 nomination](../../src/rememberstack/adapters/postgres_p1.py#L891-L927).
- The ledger's route predicate ignores component versions, so a new identity
  alone cannot fence old workers. Re-extraction also checks carriage only in
  the completing basis. If B reuses a claim from A, replacing A can therefore
  retire testimony B still publishes. Fencing writers and switching occurrences
  and currency in one transaction covers both historical repair and retries.
  [Claim predicate](../../src/rememberstack/spine/work_ledger.py#L2176-L2192),
  [re-extraction query](../../src/rememberstack/spine/lifecycle.py#L1449-L1475).

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
