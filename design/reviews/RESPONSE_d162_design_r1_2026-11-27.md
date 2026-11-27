# Response to the D162 design review, round 1

Review of PR #681 at `b69dd3e90`, after the move to one grid per version.
The review reported no P0 findings. All P1 and P2 findings and the nit are accepted.

## P1

- **Reclaimed attempts.** The ledger checks the attempt at completion, after
  CHUNK has inserted rows. EMBED_CHUNK also writes vectors and stamps separately.
  The design requires CHUNK and EMBED_CHUNK to check the current running
  attempt under lock in their data write transactions. Recovery takes the
  same row locks.
  [Chunk insert](../../src/rememberstack/spine/chunk_catalog.py#L74-L80) ·
  [Vector and stamp writes](../../src/rememberstack/workers/e1.py#L569-L607) ·
  [Completion predicate](../../src/rememberstack/spine/work_ledger.py#L2271-L2278) ·
  [Recovery](../../plan/designs/split_oversized_chunks_design.md#3-recover-an-unpublished-version)
- **Repeat replay.** A provider-confirmed size rejection leaves a dead letter
  when the rejected input still passes qualified local fit. A rebuilt grid
  can replay again only when revised qualification turns that input from fit
  to oversize. This preserves paid partial vectors and retry limits under the
  same fit decision. The rule applies to both replay entry paths.
  [D80 input preparation](../../src/rememberstack/workers/e1.py#L278-L295) ·
  [Recovery](../../plan/designs/split_oversized_chunks_design.md#3-recover-an-unpublished-version)
- **Claimify reuse.** Failed card remapping can lead to fresh Selection with
  the same input hash. Claimify keeps that hash for lookup and compares the
  target's frozen decisions and the preceding producers' published cards
  before reattaching claims. Card equivalence uses remapped passages, and
  two committed empty results compare equal.
  [Selection fallback](../../src/rememberstack/workers/e2.py#L655-L695) ·
  [Claimify hash](../../src/rememberstack/core/selection_references.py#L70-L86) ·
  [Reuse contract](../../plan/designs/document_reference_context_design.md#reuse-including-negative-dependencies)

## P2

- **Last-attempt CHUNK and generic replay.** Claiming requires `attempts < max_attempts`.
  Give the rescheduled CHUNK row a bounded allowance above its consumed count.
  The generic dead-letter replay entry point matches the observed size-failure
  attempt and announces CHUNK. Its success reopens EMBED_CHUNK.
  [Claim predicate](../../src/rememberstack/spine/work_ledger.py#L2154-L2167) ·
  [Generic replay](../../src/rememberstack/spine/work_ledger.py#L1026-L1098) ·
  [Replay contract](../../plan/designs/orchestration_design.md#6-dead-letter-operations)
- **Whole blocks and fragment ordinals.** Whole blocks are the unit of base
  packing. A final fragment records the first and last intersected blocks.
  A slice entirely inside a gap uses the preceding block ordinal for both
  fields. Character offsets define the actual slice.
  [Base packer](../../src/rememberstack/core/chunker.py#L198-L216) ·
  [Conversion eligibility](../../plan/designs/format_conversion_design.md#45-extraction-eligibility) ·
  [Schema](../../plan/designs/postgres_schema_design.md#7-e1--chunks)

## Nit

- Readiness reports missing **expected downstream stages**. The requested
  processing layers determine which stages appear.
  [Readiness filter](../../src/rememberstack/spine/readiness.py#L325-L332)
