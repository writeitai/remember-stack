# Oversized E1 chunks

## Why split at E1

Two of 40 hosted WriteIt documents reportedly dead-lettered at `embed_chunk`.
One chunk had 71,710 characters and 3,021 whitespace tokens. The packer counts
whitespace tokens and can emit a whole oversized block. Dense YAML, OCR tables
and long turns can exceed a model's limit under that packing rule.
[Base packer](../../src/rememberstack/core/chunker.py#L173-L240).

Hosted probes reportedly accepted a 19,960-token input and a 45,919-token
batch on Qwen3-Embedding-8B via OpenRouter `nebius/bf16`. Larger probes returned
generic 429s. Acceptance alone gives no evidence of full delivery, and a 429
alone gives no cause. Qualify the actual route and its request limits.
[Adapter retries](../../src/rememberstack/adapters/openrouter.py#L1079-L1124).

A smaller whitespace budget still admits oversized singletons. A 16 KiB
limit would cut a 46,833-byte LoCoMo OCR sample. Splitting in E0 would change
the block map, sections and reuse identities. Checking complete D80 inputs
in E1 confines changes to over-limit spans and the fields that depend on
their order, count or extraction windows. D80 can add a larger header to a
shorter body, so each piece needs its own fit check.
[D80 rendering](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).

## Existing version behavior

At the same source identity, D55 registers changed bytes as a new version
and returns the existing latest version for identical bytes. With explicit
D140 keys, a new key creates a version and an identical retry of the latest
key returns it. Convert is keyed by version ID. Structure makes the new
version current by version number. Configured D143 claims and facts
admission skips earlier versions whose embed stage never succeeded.
[Ingest](../../src/rememberstack/spine/document_catalog.py#L221-L250) ·
[Convert key](../../src/rememberstack/spine/document_catalog.py#L360-L375) ·
[Explicit keys](../../src/rememberstack/spine/effective_time.py#L218-L249) ·
[Current pointer](../../src/rememberstack/spine/document_catalog.py#L1383-L1393) ·
[Admission](../../src/rememberstack/spine/processing_layers.py#L734-L750) ·
[Source completion](../../src/rememberstack/spine/processing_layers.py#L778-L808).

D140 matches persisted chunks by date-free identity in non-deleted versions,
including embed failures. The earliest known origin no later than the new
version's source date wins. It records when the words were observed, even
when that observation produced no claims. Without an eligible match the
chunk uses its own version's date or remains undated.
[Origin query](../../src/rememberstack/spine/chunk_catalog.py#L332-L345) ·
[Origin rule](../../src/rememberstack/core/text_origin.py#L54-L85).

An embed dead letter prevents pipeline readiness and the claims handoff.
E0 can already have published the structured source and its chunk addresses.
Search can see a vector from a batch committed before the failure.
[Claims handoff](../../src/rememberstack/spine/work_ledger.py#L294-L329) ·
[E0 publication](../../src/rememberstack/spine/document_catalog.py#L827-L852) ·
[Chunk visibility](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L700-L719) ·
[P1 qualification](../../src/rememberstack/adapters/postgres_p1.py#L833-L852).

## Text origin at a split boundary

An edit to the first of three list items can make the whitespace packer join
it with the second item. Splitting the oversized candidate can restore the
second item's exact old window. The third item consumes the same neighbour
text, passages and origins. Both E1 key builders currently take the
neighbour's stored content hash, so the split discriminator alone would
change the third item's Selection key and make D140 fall back to the edit
date. The legacy hash can preserve both keys when the donor consumed the
same window.
[Packer](../../src/rememberstack/core/chunker.py#L198-L238) ·
[Key builders](../../src/rememberstack/workers/e1.py#L656-L765) ·
[Selection input](../../src/rememberstack/workers/e2.py#L752-L787).

A local probe replaced an HTML comment between two paragraphs with the
same number of spaces and simulated a cut that restored the two-paragraph
window. The blockizer skipped the comment, the packer hashed only the
blocks, and the relative converter-origin intervals stayed the same.
Selection received the full Markdown slice, including the gap. With the
legacy hash the target kept its Selection key and inherited January's date
in February. Checking the saved donor slice and using the split hash for
the changed window gave the target February's date and a new Selection key.
The identical-window control kept January's date and both keys.
[Blockizer](../../src/rememberstack/core/blockizer.py#L165-L193) ·
[Origin hash](../../src/rememberstack/core/source_origins.py#L55-L65) ·
[Neighbour text](../../src/rememberstack/workers/e2.py#L1819-L1834).

A second probe kept the comment or cut an extended paragraph at its former
end. Both cuts restored the old Markdown, relative passage boundaries and
origins. The legacy packer hashes the ordered block hashes. Applying the
same block normalizer to each clipped passage recovered both keys and the
January origin. The exact window comparison still rejected the changed gap.
[Block normalization](../../src/rememberstack/core/blockizer.py#L86-L102) ·
[Ordered hash](../../src/rememberstack/core/chunker.py#L135-L138) ·
[Passage clipping](../../src/rememberstack/core/source_passages.py#L124-L168).

In a parent–child–parent grid, E2 reads the closest parent chunk across the
child. Legacy E1 uses consecutive document ordinals and leaves both the
content and origin slots empty. With a simulated cut that restored that
neighbour, only the empty pair recovered the legacy keys. The same pair
also found a donor for a changed neighbour. The window check rejected it
and the target took February's date with the split and origin hashes.
[E1 slots](../../src/rememberstack/workers/e1.py#L734-L765) ·
[E2 window query](../../src/rememberstack/spine/chunk_catalog.py#L373-L419).

D140 takes the earliest eligible origin. Selection picks the newest frozen
result, and remapping can succeed with no cards to compare. Verify each
actual donor's window before applying the stage's ordering. The indexed keys
find candidates, and their saved Markdown and blocks supply the comparison.
This confines the extra reads to reuse across split windows and keeps the
stored legacy keys available for exact matches.
[Origin lookup](../../src/rememberstack/spine/chunk_catalog.py#L332-L345) ·
[Selection lookup](../../src/rememberstack/spine/selection_catalog.py#L102-L138) ·
[Card remapping](../../src/rememberstack/workers/extraction_references.py#L156-L173).

## Previous-release readers

An eligible target needs a label over its own text. Legacy Selection starts
with no keep ranges and clips E0 blocks to the target and its neighbours.
It falls back to whole-window labels only when **none** of those windows
has a block intersection. A gap-only target beside a block-backed neighbour
therefore has no target label. Markdown can contain an HTML block between
two paragraphs. The converter retains it, the blockizer skips it, and the
candidate spans it. A local fixture with 180,000 repeated words in the HTML
block measured about 90,000 Qwen tokens per half. Fitting pieces cannot all
touch either paragraph while partitioning that gap.
[Text conversion](../../src/rememberstack/adapters/converters/text.py#L56-L84) ·
[Blockizer](../../src/rememberstack/core/blockizer.py#L165-L193) ·
[Candidate extent](../../src/rememberstack/core/chunker.py#L198-L215) ·
[Selection](../../src/rememberstack/workers/e2.py#L752-L763) ·
[Label fallback](../../src/rememberstack/core/source_passages.py#L124-L158).

## Durable handoffs at cutover

STRUCTURE enqueues CHUNK. CHUNK persists its grid before the ledger
completes the job and enqueues EMBED_CHUNK. A retried CHUNK can find those
rows and hand them off without packing again. Pending and backed-off ledger
rows survive a worker restart. Applying a 16,384-token fit check to an
18,000-token legacy row could dead-letter it before dispatch or committed
vector recovery. Fencing new legacy work and draining the durable handoffs
with the outgoing binding keeps the first checked embeds paired with the
new packer. The same replay branch explains why the offline candidate run
must start with the target grid absent.
[STRUCTURE follow-up](../../src/rememberstack/workers/e0.py#L2185-L2202) ·
[CHUNK write and replay](../../src/rememberstack/workers/e1.py#L132-L191) ·
[Atomic ledger completion](../../src/rememberstack/spine/work_ledger.py#L231-L264) ·
[Due-work predicate](../../src/rememberstack/spine/work_ledger.py#L2154-L2174) ·
[Vector recovery](../../src/rememberstack/workers/e1.py#L319-L336).

## Benchmark sizing

The author-reported Full-v45 audit found a 21,567-token OCR singleton
(`conv-41/D19`) and a 22,337-token bound including headers before route framing.
At least 22,528 qualified tokens are required for the benchmark binding.
A saved vector per input hash can conceal changed batch boundaries.
Compare the exact ordered requests as well as the stored rows.
[LoCoMo publication](../../benchmarks/locomo/README.md#published-results) ·
[Request batching](../../src/rememberstack/workers/e1.py#L388-L409).

For public BEAM 100K/1, flatten the batches and turn groups in file order.
Render each of the 188 messages as `{role}: {content}` in Markdown, with a
blank line between messages. Blockize it and enumerate every singleton and
every contiguous block run whose summed whitespace count is at most 400.
The largest D80-normalized body is 14,187 NFC UTF-8 bytes. Bound Qwen's
byte-level tokens by those bytes, allow up to `48 × 4 × 4 + 2 = 770` for the
D80 header and two newlines (four canonical decomposition scalars per code
point and four UTF-8 bytes per scalar), and add one end token. The result
is a conservative 14,958-token bound before route framing.
[Pinned BEAM input](https://github.com/mohammadtavakoli78/BEAM/blob/5a64c992ba05b489b891221b7bcb6b763493dde5/chats/100K/1/chat.json) ·
[D80 render](../../src/rememberstack/core/embedding_input_policy.py#L188-L234) ·
[Pinned Qwen tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).
