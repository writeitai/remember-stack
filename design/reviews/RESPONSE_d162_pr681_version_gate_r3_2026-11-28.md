# Response to PR #681 design review, round 3

Verified against `a290460a2`. Accepted both P1s, the P2 and the nit.

## P1

- **Require full input consumption.** An offline suffix-only mock consumed four of eight
  tokens per input, reported full usage, passed P1 validation and distinguished the tails.
  D162 requires endpoint evidence at the singleton and batch limits through a verified
  reject-on-overflow mode or proof of full model-side consumption for each input. Both
  prefix-only and suffix-only mocks must fail qualification.
  [OpenRouter response checks](../../src/rememberstack/adapters/openrouter.py#L1046-L1077) ·
  [P1 validation](../../src/rememberstack/ports/p1_index.py#L22-L49) ·
  [Acceptance](../../plan/designs/split_oversized_chunks_design.md#6-acceptance).
- **Scope the stock pin to E1.** Self-host shares one provider across the embedding callers.
  With a mocked endpoint, changing the shared pin changed the SQL query's outgoing request
  and top result over fixed stored vectors. D162 scopes the pin to passage jobs and checks
  baseline request parity for both query paths, claims and facts.
  [Query wiring](../../src/rememberstack/profiles/selfhost.py#L1424-L1449) ·
  [Passage wiring](../../src/rememberstack/profiles/selfhost.py#L1878-L1887) ·
  [Claim and fact wiring](../../src/rememberstack/profiles/selfhost.py#L1953-L1978) ·
  [Routing payload](../../src/rememberstack/adapters/openrouter.py#L1001-L1046) ·
  [Passage binding](../../plan/designs/split_oversized_chunks_design.md#1-check-the-complete-embedding-input).

## P2

- **Validate before claim.** Both entry points call `Worker.run_one`, which starts a ledger
  attempt before invoking the handler. An invalid binding fails at worker composition.
  Acceptance checks the queued row and its attempt state, then completes the same work after
  configuration correction through the loop and named-task dispatch.
  [Dispatch](../../src/rememberstack/profiles/selfhost.py#L1584-L1644) ·
  [Worker loop](../../src/rememberstack/adapters/selfhost/queue.py#L216-L238) ·
  [Ledger claim](../../src/rememberstack/spine/work_ledger.py#L139-L158) ·
  [Orchestration](../../plan/designs/orchestration_design.md#embed_chunk-durability-d80--minimum-contract).

## Nit

- **Describe hash lookups.** E1 §7 now describes packing the new representation and matching
  stage-specific keys. CHUNK packs the target's blocks and computes identities for its grid.
  [CHUNK worker](../../src/rememberstack/workers/e1.py#L153-L190) ·
  [Reuse mechanics](../../plan/designs/e1_chunks_design.md#7-reuse-mechanics-d56-bound-here-amendments-a1a3).

The existing OpenRouter, D80, chunker and self-host profile suites passed (302 tests).
Hosted qualification, Full-v45 OCR sizing and frozen benchmark replay remain unverified.
