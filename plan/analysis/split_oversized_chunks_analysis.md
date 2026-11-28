# Oversized E1 chunks

Two of 40 hosted WriteIt documents reportedly failed at `EMBED_CHUNK`. One
chunk had 71,710 characters and 3,021 whitespace tokens. Shrinking the base
budget leaves oversized singletons. Cutting in E0 changes blocks and keys.
E1 checks the full D80 render because even a shorter piece can gain a header.
[Base packer](../../src/rememberstack/core/chunker.py#L173-L240) ·
[D80 rendering](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).

Hosted probes reportedly accepted 19,960 input tokens and a 45,919-token
batch via OpenRouter `nebius/bf16`. Larger probes returned generic 429s.
Qualification must establish full delivery and request limits.

## Reuse and rollout

Chunk-level reuse across a split needs exact window comparisons. Block
hashes can miss a gap edit, and legacy keys leave empty slots across child
sections where E2 reads a neighbour. Inheriting those slots can propagate
a stale match after the split disappears. The owner chose a version-wide
gate. Distant intact chunks repeat paid work and take the split version's
date for relative expressions.
[E1 slots](../../src/rememberstack/workers/e1.py#L734-L765) ·
[E2 windows](../../src/rememberstack/spine/chunk_catalog.py#L373-L419).

Legacy CHUNK can hand off a saved over-limit grid on retry. Operator replay
can reopen a dead letter during the drain. The legacy passage builder leaves
a gap-only window unlabelled beside a block-backed neighbour.
[CHUNK replay](../../src/rememberstack/workers/e1.py#L132-L146) ·
[Ledger replay](../../src/rememberstack/spine/work_ledger.py#L2365-L2392) ·
[Passage labels](../../src/rememberstack/core/source_passages.py#L124-L148).

## Benchmark sizing

The author-reported Full-v45 audit found a 21,567-token OCR singleton
(`conv-41/D19`) and a 22,337-token bound with headers before route framing.
For BEAM 100K/1, render the 188 messages as `{role}: {content}` with blank
lines between them. Blockize and enumerate singletons and contiguous runs
of up to 400 whitespace tokens. The largest normalized body is 14,187 NFC
UTF-8 bytes. Add 770 bytes for D80's 48 header code points (up to four
decomposition scalars and four bytes each) and two newlines, plus one end
token. The byte-level tokenizer is bounded by 14,958 tokens before framing.
[Pinned BEAM input](https://github.com/mohammadtavakoli78/BEAM/blob/5a64c992ba05b489b891221b7bcb6b763493dde5/chats/100K/1/chat.json) ·
[Pinned tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).
