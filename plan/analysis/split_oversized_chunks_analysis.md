# Oversized E1 chunks in v0.22.4

This audit uses `c1f15b783` and supports [proposed D162](../designs/split_oversized_chunks_design.md).

## Incident and failure path

Two of 40 documents in a hosted WriteIt batch dead-lettered at `embed_chunk`:
an indentation-heavy YAML file and a Markdown decision log. One chunk hit
71,710 characters at 3,021 whitespace tokens. The packer emits large blocks
intact. Dense text can exceed the model limit within the 400-whitespace-token
target. Counts omit parser-skipped gaps, and D80 can add a location header.
[Packer](../../src/rememberstack/core/chunker.py#L173-L240),
[blockizer](../../src/rememberstack/core/blockizer.py#L177-L193),
[D80](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).

The owner-reported probe on Qwen3-Embedding-8B via OpenRouter with `nebius/bf16`
accepted 19,960 provider tokens in one input and 45,919 total in four. Isolated
60,000- and 72,000-character inputs consistently returned generic 429s. This
points to a per-input boundary near 20k tokens. The exact route boundary and
the failed rendered sizes still need measurement. The adapter retries generic
429s. Replay keeps the cuts and renders again from live source facts, so a
title rename can change the input hash when D80 includes the title.
[Retries](../../src/rememberstack/adapters/openrouter.py#L1302-L1360),
[E1 failure path](../../src/rememberstack/workers/e1.py#L444-L500),
[title lookup](../../src/rememberstack/spine/chunk_catalog.py#L270-L289).

Every converter reading feeds the same packer. The text route returns up to
1 MB verbatim. Larger-file profiles sample 50 head and 20 tail lines at 500
characters each. Search-only code/config/log ranges select model-free E0
structuring and affect E1 cuts and E2 scheduling. D137 coalesces single-block
leaves in fallback trees into a following leaf when available and in place
otherwise. OCR, tables, profile samples and long turns can yield large blocks.
[Text limits](../../src/rememberstack/adapters/converters/text.py#L26-L32),
[E0](../../src/rememberstack/workers/e0.py#L1429-L1438), [E2](../../src/rememberstack/workers/e2.py#L524-L553),
[fallback trees](../../src/rememberstack/core/structure_skeleton.py#L640-L703),
[profiles](../../src/rememberstack/adapters/converters/profile.py#L248-L276).

## Choice and scope

Lowering the whitespace target still admits large single blocks. A blanket
16 KiB byte cap for the pinned byte-level tokenizer would split a 46,833-byte
frozen LoCoMo OCR candidate. Re-blockizing E0 would change provenance and
reuse keys. Refine over-ceiling legacy candidates in E1 to preserve the E0
coordinates and fitting output. Count the rendered D80 input with the actual
tokenizer and qualify request-wide caps.
[Pinned Qwen tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).

The incident versions never completed E1. E0 marks the version and
representation ready, and public SQL can then expose their chunk IDs as soon
as CHUNK commits. Legacy E1 also writes each successful batch to P1 before
the job ends. A failed job or an empty P1 projection therefore cannot prove
the grid stayed private. A gate applied from the first chunk write supplies
that proof for new grids. Legacy recovery needs a recorded isolation boundary
covering the entire exposure window.
[E0 Ready update](../../src/rememberstack/spine/document_catalog.py#L827-L852),
[public SQL](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L719),
[batch write](../../src/rememberstack/workers/e1.py#L535-L572).

An otherwise identical embed enqueue hits the failed row because the ledger
deduplicates on stage and component version. A new delivery generation
separates work attempts from packing and paid reuse, including when the old
grid fits a wider envelope.
[Embed enqueue](../../src/rememberstack/workers/e1.py#L794-L811),
[ledger uniqueness](../../src/rememberstack/spine/work_ledger.py#L1996-L2009).

A 209-token paragraph and an appended 17,000-digit block pack together under
D58. The paragraph fits alone and keeps its D80 hash, but its second sentence
qualifies as an internal anchor. Trying the whole-block seam first preserves
that hash. The pinned Qwen pre-tokenizer isolates each digit, so the appended
block alone exceeds 16,384 model tokens.
[Packer](../../src/rememberstack/core/chunker.py#L221-L240),
[pinned tokenizer](https://huggingface.co/Qwen/Qwen3-Embedding-8B/blob/1d8ad4ca9b3dd8059ad90a75d4983776a23d44af/tokenizer.json).

D140 records the inherited date before embedding. A fresh lookup after the
original donor is deleted can move relative dates from the inherited date to
the version date. Refining verified same-version spans preserves those dates
even when the new pieces consume different neighbors. Keeping committed cuts
also avoids assigning one date to a merged piece spanning different origins.
[Origin record](../../src/rememberstack/workers/e1.py#L167-L216),
[donor lookup](../../src/rememberstack/spine/chunk_catalog.py#L332-L345),
[E2 date](../../src/rememberstack/workers/e2.py#L1794-L1801).

## Benchmark fixture audit

Reviewers reproduced public LoCoMo text and BEAM sizing. The hosted probe and
archived LoCoMo OCR audit remained author-reported. The latter covered 272 v45
sessions (frozen manifest SHA-256
`04869f1b20ed5bf6bab6e5cd1fce639a770cc82fcbf2fc12790dcae22ad7c98c`).
Across 352,993 legacy candidates it reports a 21,567-token singleton in
`conv-41/D19` before its header, including the end token, and a 22,337-token
upper bound with headers. A 16,384 cap splits that singleton. Qualify the
pinned LoCoMo binding at 22,528 or higher.
[Frozen rendering](../../benchmarks/locomo/protocol.py#L581-L629),
[binding](../../benchmarks/locomo/protocol.py#L127-L134).

For BEAM 100K/1, flatten batches, turn groups and their 188 messages in file
order. Render each message as `{role}: {content}` with a blank line between
messages. Across single blocks and contiguous block runs of up to 400
whitespace tokens, the largest D80-normalized body has 14,187 NFC UTF-8 bytes.
Add up to 770 tokens for the bounded D80 header and separators and one for
the end token. **14,958 is a conservative Qwen token bound before extra
route framing.** Whole-source JSON yields a 529,540-byte normalized body.
Qualification uses the exact retained rendering.
[Pinned BEAM chat](https://github.com/mohammadtavakoli78/BEAM/blob/5a64c992ba05b489b891221b7bcb6b763493dde5/chats/100K/1/chat.json).

Matching chunk hashes and queries on retained vectors do not exercise ingest
delivery. The current OpenRouter decoder sorts indices and checks count and
dimension, so a singleton at index `1` with a valid vector passes P1. The
strict D162 decoder rejects it. Replay from raw transport replies can detect
that difference before a protected ingest changes worker.
[Decoder](../../src/rememberstack/adapters/openrouter.py#L1034-L1077),
[P1 validation](../../src/rememberstack/ports/p1_index.py#L26-L49).

Matching IDs and quotes can coexist with different SQL values or fact windows.
Conversely, identical SQL runs generate fresh request IDs and timings.
Freeze evaluation instants and normalize the per-call telemetry to measure
result parity. Fixed vectors bypass query embedding, so qualify that path
before using them to isolate reader changes.
[SQL response](../../src/rememberstack/surfaces/query_sandbox/executor.py#L1061-L1085),
[BEAM SQL path](../../benchmarks/rs_harness_beam/answer_agent.py#L511-L525),
[LoCoMo tool dispatch](../../benchmarks/locomo/retrieval.py#L165-L202).
