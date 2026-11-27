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

Inputs above the provider's actual limit cannot complete embedding. A
conservative ceiling can sit below sizes it accepted. The incident versions
never completed E1. CHUNK can recover them from committed E0. E0 can mark
structure ready before E1 succeeds, so replay checks E1 completion.
[E0 ready update](../../src/rememberstack/spine/document_catalog.py#L827-L852),
[E1 completion](../../src/rememberstack/spine/readiness.py#L844-L881),
[ordinary replay](../../src/rememberstack/spine/work_ledger.py#L1026-L1040).

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

The BEAM 100K/1 Markdown rendering fit in 14,958 tokens with framing and
header. Whole-source JSON yielded a 529,540-byte body. Use the exact
retained inputs and public reads. Matching IDs and quotes can coexist with
different SQL values or fact windows. Fixed vectors bypass query embedding.
[BEAM provenance](../../benchmarks/rs_harness_beam/fixtures/PROVENANCE.md),
[SQL path](../../benchmarks/rs_harness_beam/answer_agent.py#L511-L525),
[LoCoMo tool dispatch](../../benchmarks/locomo/retrieval.py#L165-L202).
