# Response to PR #681 design review, round 2

Review of `149bf7e94`. No P0 findings. I accepted both findings and the nit.

## P1

- **Qualify the self-host default.** Stock Compose leaves embedding routing open,
  and an order alone enables marketplace fallback. An offline prefix-only mock
  returned the same 1,536-dimensional vector for two tails and passed adapter
  and P1 validation. The design pins one qualified default route and requires a
  common verified ceiling across every endpoint in an allowed binding. Both
  stages reject unqualified effective bindings before grid writes, carry-forward
  or dispatch. Acceptance probes the final token at singleton and batch limits.
  [Compose](../../compose.yaml#L28-L36) ·
  [Adapter](../../src/rememberstack/adapters/openrouter.py#L1001-L1077) ·
  [Vector validation](../../src/rememberstack/ports/p1_index.py#L22-L49) ·
  [D162 binding](../../plan/designs/split_oversized_chunks_design.md#1-check-the-complete-embedding-input).

## P2

- **Commit copied vectors and authority stamps together.** The P1 upsert and
  the chunk stamp open separate transactions today. Same-chunk replay can read
  the vector after the first commits. D162 puts the locked eligibility recheck,
  `chunk_search` upsert and `chunks` stamp in one PostgreSQL transaction.
  Acceptance interrupts the copy between writes, checks rollback and retries
  after a split.
  [P1 upsert](../../src/rememberstack/adapters/postgres_p1.py#L255-L285) ·
  [Chunk stamp](../../src/rememberstack/spine/chunk_catalog.py#L252-L258) ·
  [Write order](../../src/rememberstack/workers/e1.py#L569-L572) ·
  [Same-chunk replay](../../src/rememberstack/workers/e1.py#L319-L336) ·
  [D162 reuse gate](../../plan/designs/split_oversized_chunks_design.md#3-version-wide-reuse-gate).

## Nit

- **Scope the E1 identity table.** Content, Selection and text-origin keys omit
  section metadata. D80 can render the section title and role into the text hashed
  for vector reuse.
  [Selection and text-origin keys](../../src/rememberstack/workers/e1.py#L656-L721) ·
  [D80 render](../../src/rememberstack/core/embedding_input_policy.py#L204-L233) ·
  [Header fields](../../src/rememberstack/core/embedding_input_policy.py#L303-L335) ·
  [E1 identity table](../../plan/designs/e1_chunks_design.md#1-the-three-layer-model).

The existing OpenRouter and D80 tests passed (241 tests). Hosted qualification and
frozen benchmark replay remain unverified.
