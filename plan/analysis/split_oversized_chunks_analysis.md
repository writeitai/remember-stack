# Oversized E1 chunks

## Incident and scope

Two of 40 documents in a hosted WriteIt batch dead-lettered at `embed_chunk`.
One chunk was 71,710 characters and 3,021 whitespace tokens. E1 emits a large
block intact, counts whitespace tokens within blocks, and sends the normalized
slice with any D80 location header. Dense text, including YAML, OCR tables and
long turns, can exceed the provider limit even within the packing target.
[Packer](../../src/rememberstack/core/chunker.py#L173-L240) ·
[D80 renderer](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).

The owner reported accepting one input of 19,960 provider tokens and a batch
of four totaling 45,919 on Qwen3-Embedding-8B through OpenRouter
`nebius/bf16`. Larger singleton probes returned generic 429s. Those results
suggest an input limit but leave the exact limit and error classification to
route qualification. The adapter currently retries overload responses.
[OpenRouter retry loop](../../src/rememberstack/adapters/openrouter.py#L1079-L1124).

A smaller whitespace target still emits an overlong singleton. A blanket
16 KiB byte ceiling would split the 46,833-byte frozen LoCoMo OCR sample.
Moving cuts into E0 would change the block map and reuse identities. Checking
the final embedding input in E1 confines changed packing and new paid work
to the exceptional spans and their affected extraction windows.
[Proposed design](../designs/split_oversized_chunks_design.md).

D80 may select a larger header for a shorter body. One mock request used
343 serialized bytes at a line seam and 293 at a longer scalar cut. A
length search that assumes every shorter input fits can produce an invalid
piece. Render and check the actual pieces, and qualify a ceiling that
admits a single nonblank scalar with maximum header and framing.
[D80 mode selection](../../src/rememberstack/core/embedding_input_policy.py#L204-L223).

The ledger records grid completion when `embed_chunk` succeeds. E0 sets
`ready` before CHUNK, so SQL can expose chunk IDs as soon as they are written.
E1 can also write P1 batches before a later batch fails. A recovery that
changes cuts therefore needs separate evidence that readers never had access.
The ledger deduplicates by stage and component version, so restarting CHUNK
must explicitly reopen the failed embed work after any private grid replacement.
[E1 completion check](../../src/rememberstack/spine/processing_layers.py#L624-L644) ·
[Public chunk view](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L719) ·
[E1 batch write](../../src/rememberstack/workers/e1.py#L535-L572) ·
[Work identity](../../src/rememberstack/spine/work_ledger.py#L1996-L2023).

Packing can produce a new grid over the same representation and spans.
P1 upserts and E2 work deduplication use chunk IDs, so the full packing
generation belongs in a split piece's ID. D122 hashes each producer's
Selection key in order. Eligible pieces can emit different cards after
fresh extraction. Ineligible pieces always complete empty, so a stable key
for each unchanged slice preserves matching inputs for its Claimify consumers.
[Same-representation repack](../../src/tests/workers/test_reuse_lifecycle.py#L1040-L1114) ·
[P1 upsert](../../src/rememberstack/adapters/postgres_p1.py#L262-L280) ·
[E2 enqueue](../../src/rememberstack/workers/e1.py#L855-L871) ·
[Empty Selection and D122 hashing](../../src/rememberstack/workers/e2.py#L540-L606).

## Intact reuse across layouts

E1's two legacy keys include only document-adjacent chunks that share the
target's section. E2 finds the closest chunk in the same section on each
side, even across a nested section. For a parent chunk followed by a nested
child and then more parent text, an Appendix-only split leaves that E2
window intact. A probe using the existing window query and key functions
showed that switching the parent to the full E2 window on mixed grids moved
its text origin from January 1 to February 1 in both layout directions.
The parent's Selection and Claimify keys changed too. Keeping the legacy
payload for such chunks preserved all three keys and the original date.
[E1 key inputs](../../src/rememberstack/workers/e1.py#L656-L765) ·
[E2 window query](../../src/rememberstack/spine/chunk_catalog.py#L373-L420) ·
[D140 fallback](../../src/rememberstack/core/text_origin.py#L76-L85) ·
[D122 key](../../src/rememberstack/core/selection_references.py#L70-L86).

When a cut replaces an E2 neighbor, using the final window and its tagged
slice hash distinguishes the changed input even across a nested section.
The legacy path preserves fitting-grid equality and reuse for unrelated
cuts. The ordered D122 producers can still change independently.

## Benchmark sizing

The published 94.09% LoCoMo result uses retained Full-v45 ingestion at
`13ce4a5224709b3e3cda65c429d5915d47e95e89`. The author-reported audit
of 272 frozen sessions enumerated 352,993 possible block spans for sizing.
It found a 21,567-token OCR singleton body in `conv-41/D19` including the
end token and a 22,337-token upper bound with headers before route framing.
That body would split at 16,384. The benchmark check requires qualification
at 22,528 tokens or higher.
[Published run](../../benchmarks/locomo/README.md#published-results) ·
[Recorded binding](../../benchmarks/locomo/protocol.py#L127-L134) ·
[Pinned Qwen tokenizer data](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).

Reviewers reproduced the public BEAM 100K/1 sizing bound. With the 188
messages rendered as role-labelled turns, the largest normalized body
among plausible packs is 14,187 NFC UTF-8 bytes. The conservative bound
is 14,958 Qwen tokens with header and end token, before route framing.
Use the retained store and its exact rendering for the equality check.
[Pinned BEAM input](https://github.com/mohammadtavakoli78/BEAM/blob/5a64c992ba05b489b891221b7bcb6b763493dde5/chats/100K/1/chat.json) ·
[LoCoMo restore workflow](../../benchmarks/locomo/sharding/README.md#4-restore-one-conversation).

The reviews demonstrated three changes that hold embedding text and hashes
fixed. Changing ordinals from `0, 1, 2` to `0, 10, 20` drops producers
from the last chunk's previous-eight window. Moving the header into the
indexed body changes body fetches. Dropping only the typed section title
can change E2 grounding. Full rows plus the effective E2 location pairs
and D122 windows capture these dependencies. E2 stamps the Claimify hash
on completion. The offline check can derive that stamp from candidate
Selection keys before comparing the completed rows.
[E2 stamp](../../src/rememberstack/spine/claim_catalog.py#L388-L394) ·
[D122 producer lookup](../../src/rememberstack/workers/extraction_references.py#L56-L63) ·
[Body fetch](../../src/rememberstack/surfaces/query_sandbox/nomination.py#L811-L832) ·
[E2 location handling](../../src/rememberstack/workers/e2.py#L1691-L1737).
