# Oversized E1 chunks

Two of 40 hosted WriteIt documents reportedly failed at EMBED_CHUNK. One chunk had 71,710
characters and 3,021 whitespace tokens. Shrinking the base budget leaves oversized singletons.
Cutting in E0 changes blocks and keys. E1 checks the full D80 render because a cut can add a header.
[Base packing](../../src/rememberstack/core/chunker.py#L173-L240) · [D80 render](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).

Hosted probes reportedly accepted 19,960 input tokens and a 45,919-token batch via OpenRouter
`nebius/bf16`. Larger probes returned generic 429s. Qualification must verify full delivery.

Stock Compose leaves embedding routing open. An OpenRouter order by itself still permits
marketplace fallback. A prefix-only embedder can return a finite, nonzero vector of the
expected dimension, so even a small cap needs evidence that the endpoint consumes the tail.
One pinned default keeps qualification to one route. A closed allowlist gives failover
when every reachable endpoint passes the same limits.
[Compose](../../compose.yaml#L28-L36) · [Routing payload](../../src/rememberstack/adapters/openrouter.py#L1001-L1032) ·
[Vector validation](../../src/rememberstack/ports/p1_index.py#L22-L49).

A live ceiling change can alter fit between packing and dispatch. A release constant makes
that check stable. Repacking saved spans would change handles and extraction inputs, so a retry
uses the saved grid and can fail again on an oversized input. [CHUNK replay](../../src/rememberstack/workers/e1.py#L132-L146).

Per-chunk reuse across a split would require hashes for each exact window E2 consumes. Block
hashes can miss gap edits and the legacy keys can omit a neighbour read across a child section.
D65 allows several representations per version. A grid-local check leaves intact siblings free
to donate and receive reuse. A version-wide check preserves legacy keys for eligible versions.
After a split commits, missing results across that version require fresh work under existing
skip rules. New chunks use the version's date for relative expressions. A partial index makes
split-row checks independent of the number of whole chunks. The existing lineage lock orders
those checks with grid commits. Committed results and dates remain available for replay.
[D65 schema](../../src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L399-L415) ·
[E1 slots](../../src/rememberstack/workers/e1.py#L734-L765) · [E2 windows](../../src/rememberstack/spine/chunk_catalog.py#L373-L419).

The vector upsert and authority stamp currently commit separately. Replay reads `chunk_search`
by chunk ID, so an interrupted copy can be recovered after a split has made reuse ineligible.
One transaction gives the locked gate check and both writes a single commit point.
[Write order](../../src/rememberstack/workers/e1.py#L569-L572) · [Replay](../../src/rememberstack/workers/e1.py#L319-L336).

## Benchmark sizing

The reported Full-v45 audit found a 21,567-token OCR singleton (`conv-41/D19`) and a
22,337-token bound with headers before framing. For BEAM 100K/1, render the 188 messages as
`{role}: {content}` separated by blank lines. Blockize and enumerate singletons and contiguous
runs whose individual block whitespace-token counts sum to at most 400. Measure NFC UTF-8 bytes
across the entire body, including gaps. The maximum is 14,187 bytes. Add 770 bytes for D80's
48 header code points (up to four decomposition scalars of four bytes each) and two newlines,
plus one end token. The byte-level tokenizer is bounded by 14,958 tokens before route framing.
[Pinned BEAM input](https://github.com/mohammadtavakoli78/BEAM/blob/5a64c992ba05b489b891221b7bcb6b763493dde5/chats/100K/1/chat.json) · [Pinned tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).
