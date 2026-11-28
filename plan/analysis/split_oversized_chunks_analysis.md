# Oversized E1 chunks

Two of 40 hosted WriteIt documents reportedly failed at EMBED_CHUNK. One chunk had
71,710 characters and only 3,021 whitespace tokens. A smaller budget still leaves
oversized single blocks. E1 cuts after checking the full D80 input, including the location
header, so E0 block identities stay stable. [Base packing](../../src/rememberstack/core/chunker.py#L173-L240) · [D80 render](../../src/rememberstack/core/embedding_input_policy.py#L188-L234).

On 2026-11-27, the pinned hosted OpenRouter route (`qwen/qwen3-embedding-8b`, only
`nebius/bf16`, fallbacks off, zero data retention) accepted one synthetic 52,000-character
input and reported 19,960 input tokens. Single 60,000- and 72,000-character inputs returned
HTTP 429. The accepted input supports a conservative 16,384-token release constant for this
binding. An ambiguous 429 can indicate throttling, so the failures leave the exact limit
unknown. [Routing payload](../../src/rememberstack/adapters/openrouter.py#L1001-L1046).

Full-v45 pinned plain `nebius` for the same model and its adapter sent no ZDR flag for embeddings.
BEAM 100K/1 used a Compose lab deployment, which the harness reads as an existing deployment.
Both frozen replays use their recorded non-hosted bindings.
[Full-v45 profile](https://github.com/writeitai/remember-stack/blob/13ce4a5224709b3e3cda65c429d5915d47e95e89/benchmarks/locomo/protocol.py#L172-L182) · [Full-v45 adapter](https://github.com/writeitai/remember-stack/blob/13ce4a5224709b3e3cda65c429d5915d47e95e89/src/rememberstack/adapters/openrouter.py#L901-L931) · [BEAM lab](../../design/operations/p2-projection-hang-beam-smoke.md#symptom) · [BEAM reader](../../benchmarks/rs_harness_beam/answer_agent.py#L692-L704).

A split can change the text consumed across chunk boundaries, including gaps omitted by legacy
block hashes. A gate keyed by version covers intact siblings across representations and keeps
legacy keys for eligible pairs. Locking the lineage orders reuse with split grid insertion.
Writing copied vectors and stamps together prevents replay from recovering an interrupted
copy after the gate closes. [Copy and stamp](../../src/rememberstack/workers/e1.py#L569-L572) · [Same-chunk replay](../../src/rememberstack/workers/e1.py#L319-L336).
